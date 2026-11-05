"""Demo mode (FOLIO_DEMO=1): visitor isolation, private uploads, read-only demo documents, rate limits, privacy."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from folio import demo
from folio.chats import ChatStore
from folio.config import AUDIT_LOG
from folio.index import KnowledgeBase

ANSWER = "Demo answers cite their source [1]."


@pytest.fixture
def demo_server(kb, fake_llm, monkeypatch, tmp_path):
    """The real app switched into demo mode, with two small demo documents and a fake model."""
    import server

    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    (demo_dir / "Sample CV.md").write_text("# Sample CV\n\nSkills: Python, PyTorch and medical imaging. " * 5, "utf-8")
    (demo_dir / "Sample paper.md").write_text("# Sample paper\n\nThe hybrid model reaches 80.5% accuracy on MRI. " * 5, "utf-8")
    (demo_dir / "suggestions.json").write_text(json.dumps(["What skills are listed?", "How accurate is the model?"]), "utf-8")

    kb = KnowledgeBase(embedder=kb.embedder)  # the offline fake embedder, empty index
    ids = demo.load_demo_docs(kb, demo_dir)
    assert len(ids) == 2

    llm = fake_llm(answer=ANSWER, title="A title", check="not json")
    monkeypatch.setattr(server, "DEMO", True)
    monkeypatch.setattr(server, "COOKIE_SECURE", False)  # TestClient speaks plain HTTP
    monkeypatch.setattr(server, "has_server_key", lambda: True)
    monkeypatch.setattr(server, "_kb", kb)
    monkeypatch.setattr(server, "_demo_ids", set(ids))
    monkeypatch.setattr(server, "_chats", ChatStore(tmp_path / "demo.db"))
    monkeypatch.setattr(server, "_sessions", demo.Sessions())
    monkeypatch.setattr(server, "_limiter", demo.RateLimiter(per_hour=100, daily_cap=1000))
    monkeypatch.setattr("folio.demo.DEMO_DIR", demo_dir)
    monkeypatch.setattr(demo.demo_suggestions, "__defaults__", (demo_dir,))
    monkeypatch.setattr("folio.llm.client", lambda key="": llm)
    server.llm_calls = llm.calls
    return server


def visitor(server) -> TestClient:
    """A separate browser: its own cookie jar, so its own anonymous session."""
    client = TestClient(server.app)
    client.get("/api/state")  # receives the session cookie
    assert client.cookies.get(demo.COOKIE)
    return client


def ask(client, question, expect=200, **body):
    events = []
    with client.stream("POST", "/api/chat", json={"question": question, **body}) as r:
        assert r.status_code == expect, r.read()
        if expect != 200:
            return json.loads(r.read())["detail"]
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def upload(client, name, data: bytes):
    return client.post("/api/upload", files=[("files", (name, data, "text/markdown"))])


# --- Sessions and the shared state ------------------------------------------------------------


def test_session_cookie_is_httponly_and_state_describes_the_demo(demo_server):
    client = TestClient(demo_server.app)
    r = client.get("/api/state")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{demo.COOKIE}=") and "HttpOnly" in cookie
    s = r.json()
    assert s["server_key"] is True  # the key field is never shown in the demo
    assert s["demo"]["portfolio_url"].startswith("https://") and s["demo"]["max_question"] == 1000
    assert {d["name"] for d in s["docs"]} == {"Sample CV.md", "Sample paper.md"}
    assert all(d["readonly"] for d in s["docs"])
    # The cookie is issued once, not on every response.
    assert "set-cookie" not in client.get("/api/state").headers


def test_health(demo_server):
    r = TestClient(demo_server.app).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "ready": True, "demo": True}


def test_suggestions_are_curated_and_need_no_model_call(demo_server):
    r = visitor(demo_server).get("/api/suggestions").json()
    assert r == {"questions": ["What skills are listed?", "How accurate is the model?"], "source": "demo"}
    assert not any(kind == "suggest" for kind, _ in demo_server.llm_calls)


# --- Isolation ------------------------------------------------------------------------------------------


def test_visitors_cannot_see_each_others_chats(demo_server):
    alice, bob = visitor(demo_server), visitor(demo_server)
    assert alice.cookies.get(demo.COOKIE) != bob.cookies.get(demo.COOKIE)

    events = ask(alice, "What skills are listed?", fast=True)
    cid = events[0]["id"]
    assert [c["id"] for c in alice.get("/api/chats").json()["chats"]] == [cid]

    assert bob.get("/api/chats").json()["chats"] == []
    assert bob.get("/api/chats?q=skills").json()["chats"] == []
    assert bob.get(f"/api/chats/{cid}").status_code == 404
    assert bob.patch(f"/api/chats/{cid}", json={"title": "Mine now"}).status_code == 404
    assert bob.delete(f"/api/chats/{cid}").status_code == 404
    # Continuing someone else's chat id starts a new chat of one's own instead.
    bob_events = ask(bob, "And the accuracy?", chat_id=cid, fast=True)
    assert bob_events[0]["new"] and bob_events[0]["id"] != cid

    assert alice.get(f"/api/chats/{cid}").json()["title"] != "Mine now"
    assert len(alice.get(f"/api/chats/{cid}").json()["messages"]) == 2


def test_uploads_are_private_and_never_join_the_shared_index(demo_server):
    alice, bob = visitor(demo_server), visitor(demo_server)
    r = upload(alice, "alice-notes.md", b"# Notes\n\nAlice's private budget is 4,200 dollars. " * 4)
    assert r.status_code == 200
    doc = next(d for d in r.json()["docs"] if d["name"] == "alice-notes.md")
    assert not doc.get("readonly")

    assert "alice-notes.md" in {d["name"] for d in alice.get("/api/state").json()["docs"]}
    assert "alice-notes.md" not in {d["name"] for d in bob.get("/api/state").json()["docs"]}
    assert "alice-notes.md" not in {d.name for d in demo_server._kb.docs.values()}

    # Bob can't remove (or even see) Alice's file; Alice can.
    assert bob.delete(f"/api/docs/{doc['id']}").status_code == 404
    events = ask(alice, "What is the budget?", fast=True, doc=doc["id"])
    sources = next(e for e in events if e["type"] == "sources")
    assert {p["doc"] for p in sources["passages"]} == {"alice-notes.md"}
    assert alice.delete(f"/api/docs/{doc['id']}").status_code == 200


def test_demo_documents_are_read_only(demo_server):
    client = visitor(demo_server)
    doc_id = next(iter(demo_server._demo_ids))
    r = client.delete(f"/api/docs/{doc_id}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
    client.delete("/api/docs")  # "clear" only drops the visitor's own uploads
    assert len(client.get("/api/state").json()["docs"]) == 2
    r = upload(client, "Sample CV.md", b"# Replacement\n\nNot the real CV at all. " * 4)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "name_taken"


def test_upload_count_and_size_limits(demo_server, monkeypatch):
    monkeypatch.setattr(demo_server, "DEMO_MAX_UPLOAD_BYTES", 2000)
    client = visitor(demo_server)
    r = upload(client, "big.md", b"x " * 1500)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_large"
    for i in range(3):
        assert upload(client, f"note{i}.md", f"# Note {i}\n\nSmall note number {i} about apples. ".encode() * 3).status_code == 200
    r = upload(client, "note3.md", b"# One more\n\nThis is the fourth file. " * 3)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "upload_limit"
    assert client.get("/api/state").json()["demo"]["uploads_left"] == 0


def test_idle_sessions_expire_with_their_uploads_and_chats(demo_server):
    client = visitor(demo_server)
    upload(client, "temp.md", b"# Temp\n\nTemporary visitor file about pears. " * 3)
    ask(client, "What skills are listed?", fast=True)
    sid = client.cookies.get(demo.COOKIE)
    session = demo_server._sessions.get(sid)
    folder = session.upload_dir
    assert folder.is_dir() and any(folder.iterdir())

    gone = demo_server._sessions.sweep(now=time.time() + demo_server._sessions.ttl + 1)
    for s in gone:
        demo_server._chats.delete_owner(s)
    assert sid in gone and not folder.exists()
    assert demo_server._chats.list(owner=sid) == []
    # Coming back later is a fresh start: demo documents only, no old chats.
    assert len(client.get("/api/state").json()["docs"]) == 2
    assert client.get("/api/chats").json()["chats"] == []


# --- Limits ---------------------------------------------------------------------------------------------


def test_per_visitor_rate_limit_shows_a_friendly_busy_message(demo_server, monkeypatch):
    monkeypatch.setattr(demo_server, "_limiter", demo.RateLimiter(per_hour=2, daily_cap=100))
    alice, bob = visitor(demo_server), visitor(demo_server)
    ask(alice, "What skills are listed?", fast=True)
    ask(alice, "How accurate is the model?", fast=True)
    detail = ask(alice, "One more?", expect=429)
    assert detail["code"] == "demo_busy" and detail["message"] == demo.BUSY_MESSAGE and detail["retry_after"] > 0
    ask(bob, "What skills are listed?", fast=True)  # other visitors are unaffected


def test_global_daily_cap(demo_server, monkeypatch):
    monkeypatch.setattr(demo_server, "_limiter", demo.RateLimiter(per_hour=100, daily_cap=2))
    ask(visitor(demo_server), "First?", fast=True)
    ask(visitor(demo_server), "Second?", fast=True)
    assert ask(visitor(demo_server), "Third?", expect=429)["code"] == "demo_busy"


def test_long_questions_are_refused(demo_server):
    detail = ask(visitor(demo_server), "x" * 1001, expect=400)
    assert detail["code"] == "too_long"


def test_rate_limiter_window_slides_and_counts_addresses():
    rl = demo.RateLimiter(per_hour=2, daily_cap=100)
    t = 1_000_000.0
    assert rl.take("s1", "addr", now=t)[0] and rl.take("s1", "addr", now=t + 1)[0]
    allowed, retry = rl.take("s1", "addr", now=t + 2)
    assert not allowed and 3590 <= retry <= 3600
    assert rl.take("s1", "addr", now=t + 3600)[0]  # the first question left the window
    # Clearing cookies (new session id) doesn't escape the address limit (3x the per-visitor limit).
    rl2 = demo.RateLimiter(per_hour=2, daily_cap=100)
    results = [rl2.take(f"fresh-session-{i}", "same-addr", now=t + i)[0] for i in range(8)]
    assert results.count(True) == 6


# --- Privacy ----------------------------------------------------------------------------------------------


def test_audit_log_has_a_hashed_session_and_no_address(demo_server):
    AUDIT_LOG.unlink(missing_ok=True)
    client = visitor(demo_server)
    ask(client, "What skills are listed?", fast=True)
    entries = [json.loads(line) for line in AUDIT_LOG.read_text("utf-8").splitlines()]
    sid = client.cookies.get(demo.COOKIE)
    entry = entries[-1]
    assert entry["session"] and entry["session"] != sid and sid not in json.dumps(entries)
    assert not {"ip", "address", "client", "host"} & set(entry)
    assert "testclient" not in json.dumps(entries)


def test_audit_entries_older_than_seven_days_are_pruned(tmp_path):
    log = tmp_path / "audit.jsonl"
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    stamp = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S%z")
    log.write_text("".join(json.dumps({"ts": stamp(d), "question": f"q{d}"}) + "\n" for d in (0, 3, 6, 8, 30)), "utf-8")
    assert demo.prune_audit(log, days=7, now=now) == 2
    assert [json.loads(line)["question"] for line in log.read_text("utf-8").splitlines()] == ["q0", "q3", "q6"]


def test_local_mode_is_unchanged_single_user(kb, fake_llm, monkeypatch, tmp_path):
    """With demo mode off there are no cookies, no limits and no demo fields."""
    import server

    monkeypatch.setattr(server, "_kb", kb)
    client = TestClient(server.app)
    r = client.get("/api/state")
    assert "set-cookie" not in r.headers and "demo" not in r.json()
    assert r.json()["user"]["name"] != "Visitor"
