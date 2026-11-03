"""Saved chats in SQLite (.cache/folio.db), using only the standard library.

Each assistant message stores a JSON payload with everything the UI needs to
re-render the answer exactly: passages, citations, claim check, conflicts, gap
report, table, confidence, model and timing.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    payload_json TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE INDEX IF NOT EXISTS chats_updated ON chats(updated_at DESC);
"""

TITLE_MAX = 40
_FILLER = re.compile(r"^(please|hey|hi|ok(ay)?|so|can you|could you|would you|i want to know|tell me)\b[\s,]*", re.IGNORECASE)


def fallback_title(question: str, limit: int = TITLE_MAX) -> str:
    """A short, tidy title from the first question: no filler, no trailing '?', cut at a word."""
    text = re.sub(r"\s+", " ", question).strip()
    for _ in range(2):
        text = _FILLER.sub("", text).strip()
    text = text.rstrip(" ?.!:;,") or "New chat"
    text = text[0].upper() + text[1:]
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") or text[: limit - 1]
    return cut + "…"


def clean_llm_title(raw: str, limit: int = TITLE_MAX) -> str | None:
    """Accept a model-proposed title only if it is short plain text."""
    t = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    t = t.strip().splitlines()[0] if t.strip() else ""
    t = re.sub(r"^(title|chat title)\s*:\s*", "", t, flags=re.IGNORECASE)
    t = t.strip().strip("\"'“”*#`").rstrip(".").strip()
    if not t or len(t) > limit or len(t.split()) > 8:
        return None
    return t[0].upper() + t[1:]


class ChatStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- chats ---------------------------------------------------------------
    def create(self, title: str) -> dict:
        now = time.time()
        chat = {"id": uuid.uuid4().hex[:16], "title": title, "created_at": now, "updated_at": now}
        with self._lock:
            self._db.execute("INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                             (chat["id"], chat["title"], now, now))
        return chat

    def get(self, chat_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def list(self, query: str = "", limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM chats", []
        if query.strip():
            # Match titles or anything said in the chat.
            sql += (" WHERE title LIKE ? ESCAPE '\\' OR id IN "
                    "(SELECT chat_id FROM messages WHERE content LIKE ? ESCAPE '\\')")
            like = "%" + re.sub(r"([%_\\])", r"\\\1", query.strip()) + "%"
            args = [like, like]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, [*args, limit]).fetchall()]

    def rename(self, chat_id: str, title: str) -> dict | None:
        with self._lock:
            cur = self._db.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
        return self.get(chat_id) if cur.rowcount else None

    def set_title_if(self, chat_id: str, expected: str, title: str) -> bool:
        """Replace the title only if it is still `expected` (i.e. the user hasn't renamed it)."""
        with self._lock:
            cur = self._db.execute("UPDATE chats SET title = ? WHERE id = ? AND title = ?", (title, chat_id, expected))
        return bool(cur.rowcount)

    def delete(self, chat_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return bool(cur.rowcount)

    # --- messages ------------------------------------------------------------
    def add_message(self, chat_id: str, role: str, content: str, payload: dict | None = None) -> int:
        now = time.time()
        data = json.dumps(payload, ensure_ascii=False) if payload is not None else None
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO messages (chat_id, role, content, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (chat_id, role, content, data, now))
            self._db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        return int(cur.lastrowid)

    def messages(self, chat_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json")) if d["payload_json"] else None
            out.append(d)
        return out

    def turns(self, chat_id: str, limit: int = 4) -> list[dict]:
        """Recent completed question/answer pairs, oldest first, for follow-up rewriting."""
        pairs, question = [], None
        for m in self.messages(chat_id):
            if m["role"] == "user":
                question = m["content"]
            elif question is not None and m["content"].strip():
                pairs.append({"q": question, "a": m["content"]})
                question = None
        return pairs[-limit:]
