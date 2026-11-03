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
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from pydantic import BaseModel, Field

from folio import llm
from folio.chats import ChatStore, fallback_title
from folio.config import (CACHE_DIR, CHAT_DB, EMBED_MODEL, LLM_MODEL, MAX_FILE_BYTES, USER_INITIAL, USER_NAME,
                          has_server_key)
from folio.index import KnowledgeBase
from folio.loaders import SUPPORTED, LoaderError, load_document
from folio.pipeline import Request, generate_title, provider_error, run, suggest_questions

STATIC_DIR = Path(__file__).parent / "static"

_kb: KnowledgeBase | None = None
_lock = threading.Lock()  # guards _kb (index changes and searches)
_chats = ChatStore(CHAT_DB)


def _ensure_kb() -> KnowledgeBase:
    """Load the embedding model and saved documents on first use (call with _lock held)."""
    global _kb
    if _kb is None:
        kb = KnowledgeBase()
        if kb.load(CACHE_DIR):  # migrated or re-embedded: write the new format
            kb.save(CACHE_DIR)
        _kb = kb
    return _kb


def _warm_up() -> None:
    try:
        with _lock:
            _ensure_kb()
    except Exception:
        pass  # reported on the first real request instead


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warm_up, daemon=True).start()
    yield


app = FastAPI(title="Folio", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _fail(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _key_or_fail(header_key: str) -> str:
    key = header_key.strip()
    if not has_server_key() and not key:
        raise _fail(401, "missing_key", "Add a Groq API key in Advanced settings to get answers.")
    return key


def _peek_docs() -> list[dict]:
    """Saved document summaries without loading the model (used while warming up)."""
    try:
        payload = json.loads((CACHE_DIR / "docs.json").read_text("utf-8"))
        if payload.get("version") == 2:
            return [{k: d.get(k) for k in ("id", "name", "type", "added_at")} | {"chunks": len(d.get("chunks", []))}
                    for d in payload.get("docs", [])]
        return [{"id": "", "name": d["name"], "type": "", "chunks": len(d.get("chunks", [])), "added_at": 0}
                for d in payload.get("docs", [])]
    except Exception:
        return []


def _docs_payload() -> dict:
    docs = [d.summary() for d in _kb.docs.values()] if _kb is not None else _peek_docs()
    return {"docs": docs, "total_chunks": sum(d["chunks"] for d in docs), "ready": _kb is not None}


# --- Pages ------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --- Documents ------------------------------------------------------------------------
@app.get("/api/state")
def state() -> dict:
    return {
        **_docs_payload(),
        "server_key": has_server_key(),
        "default_model": LLM_MODEL,
        "embed_model": EMBED_MODEL,
        "accept": sorted(SUPPORTED),
        "user": {"name": USER_NAME, "initial": USER_INITIAL},
    }


@app.post("/api/upload")
def upload(files: list[UploadFile] = File(...)) -> dict:
    """Index one or more files. Returns a result per file; 422 only if none could be indexed."""
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
def remove_doc(doc_id: str) -> dict:
    with _lock:
        kb = _ensure_kb()
        if not kb.remove(doc_id):
            raise _fail(404, "not_found", "That document isn't indexed.")
        kb.save(CACHE_DIR)
        payload = _docs_payload()
    _refresh_suggestions_soon()
    return payload


@app.delete("/api/docs")
def clear_docs() -> dict:
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
    if has_server_key():
        threading.Thread(target=lambda: _safe(_make_suggestions), daemon=True).start()


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


@app.get("/api/suggestions")
def suggestions(x_api_key: str = Header(default="")) -> dict:
    return _make_suggestions(x_api_key.strip())


# --- Saved chats --------------------------------------------------------------------------------
class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@app.get("/api/chats")
def list_chats(q: str = "") -> dict:
    return {"chats": _chats.list(q[:200])}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str) -> dict:
    chat = _chats.get(chat_id)
    if chat is None:
        raise _fail(404, "not_found", "That chat doesn't exist.")
    return {**chat, "messages": _chats.messages(chat_id)}


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, body: RenameBody) -> dict:
    title = " ".join(body.title.split())
    chat = _chats.rename(chat_id, title) if title else None
    if chat is None:
        raise _fail(404, "not_found", "That chat doesn't exist.")
    return chat


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str) -> dict:
    if not _chats.delete(chat_id):
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
def chat(body: ChatBody, x_api_key: str = Header(default="")) -> StreamingResponse:
    """Stream the answer as Server-Sent Events (one JSON object per `data:` line).

    Without a chat_id (or with an unknown one) a new chat is created; its id is
    the first event. Both the question and the answer are saved to that chat.
    """
    key = _key_or_fail(x_api_key)
    question = body.question.strip()
    if not question:
        raise _fail(400, "empty_question", "Type a question first.")
    with _lock:
        kb = _ensure_kb()

    chat = _chats.get(body.chat_id) if body.chat_id else None
    is_new = chat is None
    if is_new:
        chat = _chats.create(fallback_title(question))
    history = _chats.turns(chat["id"])
    _chats.add_message(chat["id"], "user", question)

    model = body.model or LLM_MODEL
    req = Request(question=question, model=model, k=body.k, fast=body.fast, doc=body.doc or None,
                  chat_id=chat["id"], api_key=key)
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
        gen = run(kb, _lock, req, history)
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
