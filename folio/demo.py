"""Public demo mode (FOLIO_DEMO=1): anonymous visitor sessions, rate limits, read-only demo documents.

Everything here is used only when demo mode is on; local use never touches it.

- Each visitor gets an anonymous session id in a secure, HttpOnly cookie. Their
  chats, uploads and document list are scoped to it; nobody sees another
  visitor's data.
- The documents in demo/ form a shared, read-only index. A visitor who uploads
  files gets a private copy of that index plus their files (the demo vectors are
  reused, only the new files are embedded). Uploads live in a temp directory and
  are deleted, with the visitor's chats, after an hour of inactivity.
- Rate limits protect the server's API quota: questions per visitor per hour
  (by session and, in memory only, by a hashed client address so clearing
  cookies doesn't reset it) and a global daily cap.
- Nothing personal is persisted: the audit log gets a salted hash of the
  session id, never an IP address, and entries older than 7 days are pruned.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import (AUDIT_LOG, AUDIT_RETENTION_DAYS, DEMO_DAILY_CAP, DEMO_DIR, DEMO_PER_HOUR, DEMO_SESSION_TTL)
from .index import KnowledgeBase
from .loaders import SUPPORTED, LoaderError, load_document

COOKIE = "folio_sid"
_SID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
# Per-process salt: hashes can't be linked across restarts or reversed to a session id.
_SALT = secrets.token_bytes(16)
BUSY_MESSAGE = "The demo is busy. Try again in a few minutes."


def new_sid() -> str:
    return secrets.token_urlsafe(24)


def valid_sid(value: str | None) -> bool:
    return bool(value and _SID.match(value))


def hashed(value: str) -> str:
    """Salted, truncated hash: enough to group one visitor's audit entries, nothing more."""
    return hashlib.sha256(_SALT + value.encode()).hexdigest()[:16]


# --- Demo documents -------------------------------------------------------------------------


def load_demo_docs(kb: KnowledgeBase, directory: Path = DEMO_DIR) -> list[str]:
    """Index every supported file in demo/ into kb. Returns the doc ids (read-only for visitors)."""
    ids = []
    if not directory.is_dir():
        return ids
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in SUPPORTED or path.name.startswith("."):
            continue
        try:
            file_type, sections = load_document(path.name, path.read_bytes())
            ids.append(kb.add(path.name, file_type, sections).id)
        except (LoaderError, ValueError):
            continue
    return ids


def demo_suggestions(directory: Path = DEMO_DIR) -> list[str]:
    """Curated starter questions (demo/suggestions.json), written for the demo documents only."""
    try:
        data = json.loads((directory / "suggestions.json").read_text("utf-8"))
        return [q for q in data if isinstance(q, str) and q.strip()][:3]
    except Exception:
        return []


def clone_kb(base: KnowledgeBase) -> KnowledgeBase:
    """A private knowledge base holding the same documents and vectors as `base` (no re-embedding)."""
    kb = KnowledgeBase(embedder=base.embedder)
    kb.docs = dict(base.docs)
    kb._install(base._vectors_by_doc())
    return kb


# --- Sessions -------------------------------------------------------------------------------


@dataclass
class Session:
    id: str
    last_seen: float = field(default_factory=time.time)
    kb: KnowledgeBase | None = None  # None until the visitor uploads something
    lock: threading.Lock = field(default_factory=threading.Lock)
    upload_dir: Path | None = None
    uploads: int = 0

    @property
    def tag(self) -> str:
        return hashed(self.id)


class Sessions:
    def __init__(self, ttl: int = DEMO_SESSION_TTL):
        self.ttl = ttl
        self._by_id: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.root = Path(tempfile.mkdtemp(prefix="folio-demo-uploads-"))

    def touch(self, sid: str) -> Session:
        with self._lock:
            s = self._by_id.get(sid)
            if s is None:
                s = self._by_id[sid] = Session(sid)
            s.last_seen = time.time()
            return s

    def get(self, sid: str) -> Session | None:
        with self._lock:
            return self._by_id.get(sid)

    def upload_dir(self, s: Session) -> Path:
        if s.upload_dir is None:
            s.upload_dir = self.root / s.tag
            s.upload_dir.mkdir(parents=True, exist_ok=True)
        return s.upload_dir

    def sweep(self, now: float | None = None) -> list[str]:
        """Forget sessions idle longer than the TTL and delete their uploads. Returns their ids."""
        now = now or time.time()
        with self._lock:
            gone = [sid for sid, s in self._by_id.items() if now - s.last_seen > self.ttl]
            expired = [self._by_id.pop(sid) for sid in gone]
        for s in expired:
            if s.upload_dir is not None:
                shutil.rmtree(s.upload_dir, ignore_errors=True)
        return gone

    def __len__(self) -> int:
        return len(self._by_id)


# --- Rate limits ----------------------------------------------------------------------------


class RateLimiter:
    """Sliding one-hour window per key (session and hashed address) plus a global daily cap."""

    def __init__(self, per_hour: int = DEMO_PER_HOUR, daily_cap: int = DEMO_DAILY_CAP, window: int = 3600):
        self.per_hour, self.daily_cap, self.window = per_hour, daily_cap, window
        self._hits: dict[str, deque] = {}
        self._day, self._today = "", 0
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def take(self, session_key: str, address_key: str = "", now: float | None = None) -> tuple[bool, int]:
        """Count one question if allowed. Returns (allowed, seconds until a retry may succeed)."""
        now = now or time.time()
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if day != self._day:
                self._day, self._today = day, 0
            if self._today >= self.daily_cap:
                tomorrow = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0) + timedelta(days=1)
                return False, int(tomorrow.timestamp() - now)
            # The address limit is looser, so a shared network (office, campus) isn't blocked by one person.
            limits = [(f"s:{session_key}", self.per_hour)]
            if address_key:
                limits.append((f"a:{address_key}", self.per_hour * 3))
            for key, limit in limits:
                q = self._recent(key, now)
                if len(q) >= limit:
                    return False, max(1, int(self.window - (now - q[0])))
            for key, _ in limits:
                self._hits[key].append(now)
            self._today += 1
            return True, 0

    def prune(self, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            for key in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]:
                del self._hits[key]


# --- Audit retention ---------------------------------------------------------------------------


def prune_audit(path: Path = AUDIT_LOG, days: int = AUDIT_RETENTION_DAYS, now: datetime | None = None) -> int:
    """Drop audit entries older than `days`. Returns how many were removed."""
    try:
        lines = path.read_text("utf-8").splitlines()
    except FileNotFoundError:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    keep = []
    for line in lines:
        try:
            ts = datetime.strptime(json.loads(line)["ts"], "%Y-%m-%dT%H:%M:%S%z")
        except Exception:
            continue  # unreadable entries are dropped too
        if ts >= cutoff:
            keep.append(line)
    if len(keep) != len(lines):
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(f"{line}\n" for line in keep), "utf-8")
        tmp.replace(path)
    return len(lines) - len(keep)
