"""
server.py — FastAPI backend + static frontend for Folio.

Run it with:
    uvicorn server:app --reload
then open http://localhost:8000

The pipeline lives in the folio/ package. This file is the HTTP layer:
documents (upload, list, remove), models, saved chats (SQLite), suggested
questions, and a streaming (SSE) chat endpoint.

API key: if LLM_API_KEY is set (environment or .env) it is used for every
request and the browser never sees it. Otherwise the browser may send its own
key per request in the "x-api-key" header. Keys are never logged, stored or
returned.

Demo mode (FOLIO_DEMO=1, used on Hugging Face Spaces) adds anonymous visitor
sessions, read-only demo documents, private uploads and rate limits; see
folio/demo.py. With it off, behaviour is exactly the local single-user app.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Request as HttpRequest, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from pydantic import BaseModel, Field

from folio import demo, llm
from folio.chats import ChatStore, fallback_title
from folio.config import (CACHE_DIR, CHAT_DB, COOKIE_SECURE, DEMO, DEMO_AUTHOR, DEMO_DAILY_CAP, DEMO_MAX_QUESTION,
                          DEMO_MAX_UPLOAD_BYTES, DEMO_MAX_UPLOADS, DEMO_PER_HOUR, DEMO_SESSION_TTL, EMBED_MODEL,
                          LLM_MODEL, MAX_FILE_BYTES, PORTFOLIO_URL, REPO_URL, USER_INITIAL, USER_NAME, has_server_key)
from folio.index import KnowledgeBase
from folio.loaders import SUPPORTED, LoaderError, load_document
from folio.pipeline import Request, generate_title, provider_error, run, suggest_questions
from folio.types import doc_id_for

STATIC_DIR = Path(__file__).parent / "static"

_kb: KnowledgeBase | None = None
_lock = threading.Lock()  # guards _kb (index changes and searches)
_chats = ChatStore(CHAT_DB)

# Demo mode only.
_sessions = demo.Sessions()
_limiter = demo.RateLimiter()
_demo_ids: set[str] = set()  # read-only demo document ids


def _ensure_kb() -> KnowledgeBase:
    """Load the embedding model and saved documents on first use (call with _lock held).

    In demo mode the shared index is built from demo/ instead, and never saved.
    """
    global _kb
    if _kb is None:
        kb = KnowledgeBase()
        if DEMO:
            _demo_ids.update(demo.load_demo_docs(kb))
        elif kb.load(CACHE_DIR):  # migrated or re-embedded: write the new format
            kb.save(CACHE_DIR)
        _kb = kb
    return _kb


def _warm_up() -> None:
    try:
        with _lock:
            _ensure_kb()
    except Exception:
        pass  # reported on the first real request instead


def _sweeper() -> None:
    """Demo housekeeping: expire idle visitors (uploads and chats) and prune the audit log."""
    last_prune = 0.0
    while True:
        time.sleep(60)
        try:
            for sid in _sessions.sweep():
                _chats.delete_owner(sid)
            _limiter.prune()
            if time.time() - last_prune > 3600:
                demo.prune_audit()
                last_prune = time.time()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm_up, daemon=True).start()
    if DEMO:
        demo.prune_audit()
        threading.Thread(target=_sweeper, daemon=True).start()
    yield


app = FastAPI(title="Folio", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def visitor_session(request: HttpRequest, call_next):
    """Demo mode: give every visitor an anonymous session cookie (secure, HttpOnly)."""
    if not DEMO:
        return await call_next(request)
    sid = request.cookies.get(demo.COOKIE)
    fresh = not demo.valid_sid(sid)
    if fresh:
        sid = demo.new_sid()
    request.state.sid = sid
    response = await call_next(request)
    if fresh:
        response.set_cookie(demo.COOKIE, sid, max_age=7 * 24 * 3600, httponly=True, secure=COOKIE_SECURE,
                            samesite="none" if COOKIE_SECURE else "lax", path="/")
    return response


def _fail(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _key_or_fail(header_key: str) -> str:
    if DEMO:  # only the server's secret; visitors never send or see a key
        if not has_server_key():
            raise _fail(503, "demo_unconfigured", "This demo isn't connected to a model right now.")
        return ""
    key = header_key.strip()
    if not has_server_key() and not key:
        raise _fail(401, "missing_key", "Add a Groq API key in Advanced settings to get answers.")
    return key


# --- Visitors (demo) ------------------------------------------------------------------------
def _visitor(request: HttpRequest) -> demo.Session | None:
    return _sessions.touch(request.state.sid) if DEMO else None


def _owner(request: HttpRequest) -> str:
    """Whose chats these are: "" locally, the visitor's session id in demo mode."""
    return request.state.sid if DEMO else ""


def _kb_for(visitor: demo.Session | None) -> tuple[KnowledgeBase, threading.Lock]:
    """The visitor's private index if they uploaded files, otherwise the shared one."""
    if visitor is not None and visitor.kb is not None:
        return visitor.kb, visitor.lock
    with _lock:
        return _ensure_kb(), _lock


def _client_address(request: HttpRequest) -> str:
    """Hashed, in memory only (rate limiting). Spaces sits behind a proxy that sets X-Forwarded-For."""
    raw = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    raw = raw or (request.client.host if request.client else "")
    return demo.hashed(raw) if raw else ""


def _peek_docs() -> list[dict]:
    """Saved document summaries without loading the model (used while warming up)."""
    if DEMO:
        return []
    try:
        payload = json.loads((CACHE_DIR / "docs.json").read_text("utf-8"))
        if payload.get("version") == 2:
            return [{k: d.get(k) for k in ("id", "name", "type", "added_at")} | {"chunks": len(d.get("chunks", []))}
                    for d in payload.get("docs", [])]
        return [{"id": "", "name": d["name"], "type": "", "chunks": len(d.get("chunks", [])), "added_at": 0}
                for d in payload.get("docs", [])]
    except Exception:
        return []


def _docs_payload(visitor: demo.Session | None = None) -> dict:
    kb = visitor.kb if visitor is not None and visitor.kb is not None else _kb
    if kb is None:
        docs = _peek_docs()
    else:
        docs = [d.summary() | ({"readonly": True} if d.id in _demo_ids else {}) for d in kb.docs.values()]
    return {"docs": docs, "total_chunks": sum(d["chunks"] for d in docs), "ready": _kb is not None}


# --- Pages ------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "ready": _kb is not None, "demo": DEMO}


# --- Documents ------------------------------------------------------------------------
@app.get("/api/state")
def state(request: HttpRequest) -> dict:
    visitor = _visitor(request)
    with (visitor.lock if visitor is not None else _lock):
        docs = _docs_payload(visitor)
    out = {
        **docs,
        "server_key": has_server_key() or DEMO,  # demo: the key field is never shown
        "default_model": LLM_MODEL,
        "embed_model": EMBED_MODEL,
        "accept": sorted(SUPPORTED),
        "user": {"name": "Visitor", "initial": "V"} if DEMO else {"name": USER_NAME, "initial": USER_INITIAL},
    }
    if DEMO:
        out["demo"] = {
            "author": DEMO_AUTHOR, "portfolio_url": PORTFOLIO_URL, "repo_url": REPO_URL,
            "max_question": DEMO_MAX_QUESTION, "max_uploads": DEMO_MAX_UPLOADS,
            "max_upload_mb": DEMO_MAX_UPLOAD_BYTES // (1024 * 1024), "uploads_left": DEMO_MAX_UPLOADS - visitor.uploads,
            "per_hour": DEMO_PER_HOUR, "session_minutes": DEMO_SESSION_TTL // 60,
        }
    return out


def _upload_demo(visitor: demo.Session, files: list[UploadFile]) -> dict:
    """Demo uploads: private to the visitor, size- and count-limited, kept in a temp folder."""
    results = []
    for f in files:
        name = Path(f.filename or "document").name
        if visitor.uploads >= DEMO_MAX_UPLOADS:
            results.append({"name": name, "ok": False, "code": "upload_limit",
                            "message": f"The demo allows {DEMO_MAX_UPLOADS} files per visitor."})
            continue
        data = f.file.read(DEMO_MAX_UPLOAD_BYTES + 1)
        if len(data) > DEMO_MAX_UPLOAD_BYTES:
            mb = DEMO_MAX_UPLOAD_BYTES // (1024 * 1024)
            results.append({"name": name, "ok": False, "code": "too_large", "message": f"Larger than {mb} MB."})
            continue
        try:
            file_type, sections = load_document(name, data)
        except LoaderError as e:
            results.append({"name": name, "ok": False, "code": e.code, "message": e.message})
            continue
        try:
            with _lock:
                base = _ensure_kb()
            with visitor.lock:
                kb = visitor.kb or demo.clone_kb(base)
                if doc_id_for(name) in _demo_ids:
                    results.append({"name": name, "ok": False, "code": "name_taken",
                                    "message": "A demo document has this name. Rename the file and try again."})
                    continue
                doc = kb.add(name, file_type, sections)
                visitor.kb = kb
                visitor.uploads += 1
                (_sessions.upload_dir(visitor) / f"{doc.id}{Path(name).suffix.lower()}").write_bytes(data)
            results.append({"name": name, "ok": True, "doc": doc.summary()})
        except Exception:
            results.append({"name": name, "ok": False, "code": "index_failed", "message": "Couldn't index this file."})
    with visitor.lock:
        return {**_docs_payload(visitor), "results": results}


@app.post("/api/upload")
def upload(request: HttpRequest, files: list[UploadFile] = File(...)) -> dict:
    """Index one or more files. Returns a result per file; 422 only if none could be indexed."""
    visitor = _visitor(request)
    if visitor is not None:
        payload = _upload_demo(visitor, files)
        results = payload["results"]
    else:
        results = []
        for f in files:
            name = Path(f.filename or "document").name
            data = f.file.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                results.append({"name": name, "ok": False, "code": "too_large", "message": "Larger than 50 MB."})
                continue
            try:
                file_type, sections = load_document(name, data)
            except LoaderError as e:
                results.append({"name": name, "ok": False, "code": e.code, "message": e.message})
                continue
            try:
                with _lock:
                    kb = _ensure_kb()
                    doc = kb.add(name, file_type, sections)
                    kb.save(CACHE_DIR)
                results.append({"name": name, "ok": True, "doc": doc.summary()})
                _refresh_suggestions_soon()
            except Exception:
                results.append({"name": name, "ok": False, "code": "index_failed", "message": "Couldn't index this file."})

        with _lock:
            payload = {**_docs_payload(), "results": results}
    if not any(r["ok"] for r in results):
        first = results[0] if results else {"code": "no_files", "message": "No files were uploaded."}
        raise HTTPException(status_code=422, detail={"code": first["code"], "message": first["message"], "results": results})
    return payload


@app.delete("/api/docs/{doc_id}")
def remove_doc(doc_id: str, request: HttpRequest) -> dict:
    visitor = _visitor(request)
    if visitor is not None:
        if doc_id in _demo_ids:
            raise _fail(403, "read_only", "Demo documents can't be removed.")
        with visitor.lock:
            if visitor.kb is None or not visitor.kb.remove(doc_id):
                raise _fail(404, "not_found", "That document isn't indexed.")
            if visitor.upload_dir is not None:
                for p in visitor.upload_dir.glob(f"{doc_id}.*"):
                    p.unlink(missing_ok=True)
            return _docs_payload(visitor)
    with _lock:
        kb = _ensure_kb()
        if not kb.remove(doc_id):
            raise _fail(404, "not_found", "That document isn't indexed.")
        kb.save(CACHE_DIR)
        payload = _docs_payload()
    _refresh_suggestions_soon()
    return payload


@app.delete("/api/docs")
def clear_docs(request: HttpRequest) -> dict:
    visitor = _visitor(request)
    if visitor is not None:  # demo: drop the visitor's uploads; demo documents stay
        with visitor.lock:
            visitor.kb = None
            if visitor.upload_dir is not None:
                shutil.rmtree(visitor.upload_dir, ignore_errors=True)
                visitor.upload_dir = None
        with _lock:
            return _docs_payload()
    with _lock:
        kb = _ensure_kb()
        kb.clear()
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        return _docs_payload()


# --- Models ------------------------------------------------------------------------------
@app.get("/api/models")
def models(x_api_key: str = Header(default="")) -> dict:
    key = _key_or_fail(x_api_key)
    try:
        ids = llm.list_model_ids(key)
    except Exception as e:
        err = provider_error(e)
        status = 401 if err["code"] == "bad_key" else 502
        raise _fail(status, err["code"], err["message"]) from None
    options, default = llm.chat_models(ids)
    return {"models": options, "default": default}


# --- Suggested questions ----------------------------------------------------------------------
_SUGGEST_FILE = CACHE_DIR / "suggestions.json"
_suggest_lock = threading.Lock()


def _doc_set_key(kb: KnowledgeBase) -> str:
    sig = sorted(f"{d.id}:{len(d.chunks)}" for d in kb.docs.values())
    return hashlib.sha1("|".join(sig).encode()).hexdigest()[:16]


def _cached_suggestions(key: str) -> list[str] | None:
    try:
        data = json.loads(_SUGGEST_FILE.read_text("utf-8"))
        return data.get(key)
    except Exception:
        return None


def _make_suggestions(api_key: str = "") -> dict:
    """Questions for the current document set: cached per set, one cheap LLM call otherwise."""
    with _lock:
        kb = _ensure_kb()
        if not kb.docs:
            return {"questions": [], "source": "none"}
        key = _doc_set_key(kb)
        previews = [(d.name, " ".join(c.text for c in d.chunks[:2])) for d in kb.docs.values()]
        names = [d.name for d in kb.docs.values()]
    with _suggest_lock:
        cached = _cached_suggestions(key)
        if cached:
            return {"questions": cached, "source": "cache"}
        questions = None
        if has_server_key() or api_key:
            questions = suggest_questions(llm.client(api_key), "", previews)
        if not questions:
            return {"questions": _template_suggestions(names), "source": "fallback"}
        try:
            data = json.loads(_SUGGEST_FILE.read_text("utf-8")) if _SUGGEST_FILE.exists() else {}
        except Exception:
            data = {}
        data = {key: questions, **{k: v for k, v in data.items() if k != key}}
        _SUGGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SUGGEST_FILE.write_text(json.dumps(dict(list(data.items())[:20])), "utf-8")
        return {"questions": questions, "source": "llm"}


def _template_suggestions(names: list[str]) -> list[str]:
    """Used only when no model is reachable: built from the actual file names."""
    def short(n: str) -> str:
        stem = Path(n).stem
        return stem if len(stem) <= 28 else stem[:26].rsplit(" ", 1)[0] + "…"
    out = [f"Summarise {short(names[0])}"]
    if len(names) > 1:
        out.append(f"What are the key points in {short(names[1])}?")
    out.append("What figures and dates are reported?")
    return out[:3]


def _refresh_suggestions_soon() -> None:
    """After the document set changes, warm the cache in the background (server key only)."""
    if has_server_key() and not DEMO:
        threading.Thread(target=lambda: _safe(_make_suggestions), daemon=True).start()


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


@app.get("/api/suggestions")
def suggestions(x_api_key: str = Header(default="")) -> dict:
    if DEMO:  # curated questions about the demo documents; no model call, no personal details
        return {"questions": demo.demo_suggestions(), "source": "demo"}
    return _make_suggestions(x_api_key.strip())


# --- Saved chats --------------------------------------------------------------------------------
class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@app.get("/api/chats")
def list_chats(request: HttpRequest, q: str = "") -> dict:
    _visitor(request)
    return {"chats": _chats.list(q[:200], owner=_owner(request))}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str, request: HttpRequest) -> dict:
    chat = _chats.get(chat_id, owner=_owner(request))
    if chat is None:
        raise _fail(404, "not_found", "That chat doesn't exist.")
    return {**chat, "messages": _chats.messages(chat_id)}


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, body: RenameBody, request: HttpRequest) -> dict:
    title = " ".join(body.title.split())
    chat = _chats.rename(chat_id, title, owner=_owner(request)) if title else None
    if chat is None:
        raise _fail(404, "not_found", "That chat doesn't exist.")
    return chat


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str, request: HttpRequest) -> dict:
    if not _chats.delete(chat_id, owner=_owner(request)):
        raise _fail(404, "not_found", "That chat doesn't exist.")
    return {"ok": True}


def _title_later(chat_id: str, fallback: str, question: str, answer: str, api_key: str, model: str) -> None:
    """Replace the fallback title with a model-written one, unless the user renamed the chat meanwhile."""
    title = generate_title(llm.client(api_key), model, question, answer)
    if title and title != fallback:
        _chats.set_title_if(chat_id, fallback, title)


# --- Chat -------------------------------------------------------------------------------------
class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    chat_id: str | None = Field(default=None, max_length=64)
    model: str = Field(default="", max_length=200)
    k: int = Field(default=4, ge=2, le=8)
    fast: bool = False
    doc: str | None = Field(default=None, max_length=64)


def _collect(payload: dict, event: dict) -> None:
    """Fold one pipeline event into the stored answer payload."""
    t = event["type"]
    if t == "sources":
        payload["sources"] = {k: v for k, v in event.items() if k != "type"}
    elif t == "delta":
        payload["partial"] = payload.get("partial", "") + event["text"]
    elif t == "answer":
        payload["answer"] = {"text": event["text"], "citations": event.get("citations", [])}
    elif t in ("coverage", "check", "table"):
        payload[t] = {k: v for k, v in event.items() if k != "type"}
    elif t == "conflicts":
        payload["conflicts"] = event.get("items", [])
    elif t == "error":
        payload["error"] = {"code": event.get("code"), "message": event.get("message")}
    elif t == "done":
        payload["ms"] = event.get("ms")


@app.post("/api/chat")
def chat(body: ChatBody, request: HttpRequest, x_api_key: str = Header(default="")) -> StreamingResponse:
    """Stream the answer as Server-Sent Events (one JSON object per `data:` line).

    Without a chat_id (or with an unknown one) a new chat is created; its id is
    the first event. Both the question and the answer are saved to that chat.
    """
    key = _key_or_fail(x_api_key)
    question = body.question.strip()
    if not question:
        raise _fail(400, "empty_question", "Type a question first.")
    visitor = _visitor(request)
    owner = _owner(request)
    if visitor is not None:
        if len(question) > DEMO_MAX_QUESTION:
            raise _fail(400, "too_long", f"Keep questions under {DEMO_MAX_QUESTION:,} characters in the demo.")
        allowed, retry = _limiter.take(visitor.id, _client_address(request))
        if not allowed:
            raise _fail(429, "demo_busy", demo.BUSY_MESSAGE, retry_after=retry)
    kb, kb_lock = _kb_for(visitor)

    chat = _chats.get(body.chat_id, owner=owner) if body.chat_id else None
    is_new = chat is None
    if is_new:
        chat = _chats.create(fallback_title(question), owner=owner)
    history = _chats.turns(chat["id"])
    _chats.add_message(chat["id"], "user", question)

    model = body.model or LLM_MODEL
    req = Request(question=question, model=model, k=body.k, fast=body.fast, doc=body.doc or None,
                  chat_id=chat["id"], session=visitor.tag if visitor is not None else "", api_key=key)
    payload: dict = {"model": model, "model_label": llm.friendly(model)[0], "fast": body.fast}
    started = time.time()

    def save(stopped: bool) -> str:
        answer = (payload.get("answer") or {}).get("text") or payload.get("partial", "")
        payload.pop("partial", None)
        if stopped and "answer" not in payload:
            payload["answer"] = {"text": answer, "citations": []}
        payload["stopped"] = stopped
        payload.setdefault("ms", int((time.time() - started) * 1000))
        _chats.add_message(chat["id"], "assistant", answer, payload)
        return answer

    async def events():
        yield f"data: {json.dumps({'type': 'chat', 'id': chat['id'], 'title': chat['title'], 'new': is_new})}\n\n"
        gen = run(kb, kb_lock, req, history)
        finished = False
        try:
            async for event in iterate_in_threadpool(gen):
                _collect(payload, event)
                finished = finished or event["type"] in ("done", "error")
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # Client pressed Stop or went away: close the pipeline so the model
            # stream is cancelled and the request is still written to the audit log.
            try:
                await run_in_threadpool(gen.close)
            except ValueError:
                pass  # still executing in its worker thread; it closes when that step ends
            answer = save(stopped=not finished)
            if is_new and answer and not payload.get("error"):
                threading.Thread(target=_safe, args=(_title_later, chat["id"], chat["title"], question, answer,
                                                     key, model), daemon=True).start()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
