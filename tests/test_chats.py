import json
import time

import pytest
from fastapi.testclient import TestClient

from folio.chats import ChatStore, clean_llm_title, fallback_title

# --- Titles ------------------------------------------------------------------------------------


@pytest.mark.parametrize("question,title", [
    ("How well does OrbitalNet detect early crop disease?", "How well does OrbitalNet detect early…"),
    ("what are the key skills in my CV?", "What are the key skills in my CV"),
    ("Please   summarise the   leak paper.", "Summarise the leak paper"),
    ("Can you tell me the deadline?", "The deadline"),
    ("???", "New chat"),
])
def test_fallback_title(question, title):
    got = fallback_title(question)
    assert got == title
    assert len(got) <= 40


def test_fallback_title_never_cuts_mid_word():
    t = fallback_title("Supercalifragilistic words keep going and going beyond the limit")
    assert t.endswith("…") and len(t) <= 40
    assert t[:-1].split()[-1] in "Supercalifragilistic words keep going and going beyond the limit".split()


@pytest.mark.parametrize("raw,clean", [
    ('"Early crop disease results."', "Early crop disease results"),
    ("Title: Key skills in the CV", "Key skills in the CV"),
    ("<think>hmm</think>How leak detection works", "How leak detection works"),
    ("This is far too long to be a sensible chat title for the sidebar list", None),
    ("", None),
])
def test_clean_llm_title(raw, clean):
    assert clean_llm_title(raw) == clean


# --- Store ---------------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = ChatStore(tmp_path / "chats.db")
    yield s
    s.close()


def test_create_list_rename_delete(store):
    a = store.create("First")
    time.sleep(0.01)
    b = store.create("Second")
    assert [c["id"] for c in store.list()] == [b["id"], a["id"]]  # newest first
    store.add_message(a["id"], "user", "hello")  # activity moves a chat to the top
    assert store.list()[0]["id"] == a["id"]
    assert store.rename(a["id"], "Renamed")["title"] == "Renamed"
    assert store.rename("missing", "x") is None
    assert store.delete(b["id"]) and not store.delete(b["id"])
    assert [c["id"] for c in store.list()] == [a["id"]]


def test_messages_keep_payload_and_cascade(store):
    chat = store.create("Chat")
    payload = {"answer": {"text": "90.20% [1]", "citations": [{"n": 1, "start": 3, "end": 9}]},
               "check": {"ran": True, "supported": 1, "total": 1}, "ms": 1200}
    store.add_message(chat["id"], "user", "Q?")
    store.add_message(chat["id"], "assistant", "90.20% [1]", payload)
    msgs = store.messages(chat["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["payload"] is None and msgs[1]["payload"] == payload
    store.delete(chat["id"])
    assert store.messages(chat["id"]) == []


def test_search_matches_titles_and_content(store):
    a = store.create("Crop disease results")
    b = store.create("Something else")
    store.add_message(b["id"], "user", "What does the leak paper propose?")
    assert [c["id"] for c in store.list("crop")] == [a["id"]]
    assert [c["id"] for c in store.list("LEAK")] == [b["id"]]
    assert store.list("100%_") == []  # LIKE wildcards are escaped


def test_turns_pair_questions_with_answers(store):
    chat = store.create("t")
    for q, a in [("q1", "a1"), ("q2", ""), ("q3", "a3")]:
        store.add_message(chat["id"], "user", q)
        store.add_message(chat["id"], "assistant", a)
    store.add_message(chat["id"], "user", "q4 (no answer yet)")
    assert store.turns(chat["id"]) == [{"q": "q1", "a": "a1"}, {"q": "q3", "a": "a3"}]
    assert store.turns(chat["id"], limit=1) == [{"q": "q3", "a": "a3"}]


def test_title_is_only_replaced_if_unchanged(store):
    chat = store.create("Fallback")
    assert store.set_title_if(chat["id"], "Fallback", "Model title")
    store.rename(chat["id"], "User title")
    assert not store.set_title_if(chat["id"], "Model title", "Another")
    assert store.get(chat["id"])["title"] == "User title"


def test_chats_survive_reopening(tmp_path):
    s = ChatStore(tmp_path / "c.db")
    chat = s.create("Persistent")
    s.add_message(chat["id"], "user", "hi")
    s.close()
    again = ChatStore(tmp_path / "c.db")
    assert again.get(chat["id"])["title"] == "Persistent"
    assert again.messages(chat["id"])[0]["content"] == "hi"
    again.close()


# --- Chat API (mocked LLM, fake embeddings) ----------------------------------------------------------------

ANSWER = ("OrbitalNet reached **90.20%** overall accuracy [1].\n%%COVERAGE%%\n"
          '{"covered": ["accuracy"], "not_covered": []}')


@pytest.fixture
def api(kb, fake_llm, monkeypatch, tmp_path):
    import server

    llm = fake_llm(answer=[ANSWER, ANSWER, ANSWER], rewrite="What else did OrbitalNet achieve?",
                   title="Crop disease detection accuracy", check="not json",
                   suggest=json.dumps({"questions": ["What does the CV list?", "How accurate is the model?", "What does the leak paper propose?"]}))
    monkeypatch.setattr(server, "_kb", kb)
    monkeypatch.setattr(server, "_chats", ChatStore(tmp_path / "api.db"))
    monkeypatch.setattr(server, "_SUGGEST_FILE", tmp_path / "suggestions.json")
    monkeypatch.setattr("folio.llm.client", lambda key="": llm)
    client = TestClient(server.app)
    client.llm = llm
    return client


def _ask(client, question, **body):
    events = []
    with client.stream("POST", "/api/chat", json={"question": question, **body}, headers={"x-api-key": "test-key"}) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_chat_api_creates_saves_and_continues_a_chat(api):
    events = _ask(api, "How accurate is OrbitalNet?", fast=True)
    first = events[0]
    assert first["type"] == "chat" and first["new"] and first["title"] == "How accurate is OrbitalNet"
    assert events[-1]["type"] == "done"
    cid = first["id"]

    # Background title generation replaces the fallback title.
    for _ in range(50):
        if api.get(f"/api/chats/{cid}").json()["title"] != first["title"]:
            break
        time.sleep(0.02)
    assert api.get(f"/api/chats/{cid}").json()["title"] == "Crop disease detection accuracy"

    chat = api.get(f"/api/chats/{cid}").json()
    user, assistant = chat["messages"]
    assert user["role"] == "user" and assistant["role"] == "assistant"
    p = assistant["payload"]
    assert p["answer"]["text"].startswith("OrbitalNet reached") and "%%COVERAGE" not in p["answer"]["text"]
    assert p["sources"]["passages"] and p["sources"]["confidence"]["level"] in {"High", "Medium", "Low"}
    assert p["answer"]["citations"][0]["n"] == 1 and p["model"] and isinstance(p["ms"], int)
    assert p["stopped"] is False and "partial" not in p

    # A follow-up in the same chat is rewritten using the stored turn.
    events = _ask(api, "what else did it achieve?", chat_id=cid, fast=True)
    assert events[0] == {"type": "chat", "id": cid, "title": "Crop disease detection accuracy", "new": False}
    sources = next(e for e in events if e["type"] == "sources")
    assert sources["query"] == "What else did OrbitalNet achieve?"
    rewrite_call = next(kw for kind, kw in api.llm.calls if kind == "rewrite")
    assert "How accurate is OrbitalNet?" in rewrite_call["messages"][-1]["content"]
    assert len(api.get(f"/api/chats/{cid}").json()["messages"]) == 4


def test_chat_api_list_search_rename_delete(api):
    cid = _ask(api, "How accurate is OrbitalNet?", fast=True)[0]["id"]
    assert [c["id"] for c in api.get("/api/chats").json()["chats"]] == [cid]
    assert api.get("/api/chats", params={"q": "accurate"}).json()["chats"]
    assert api.get("/api/chats", params={"q": "zzz"}).json()["chats"] == []
    assert api.patch(f"/api/chats/{cid}", json={"title": "  My   title "}).json()["title"] == "My title"
    assert api.patch("/api/chats/nope", json={"title": "x"}).status_code == 404
    assert api.delete(f"/api/chats/{cid}").json() == {"ok": True}
    assert api.get(f"/api/chats/{cid}").status_code == 404
    assert api.delete(f"/api/chats/{cid}").status_code == 404


def test_unknown_chat_id_starts_a_new_chat(api):
    events = _ask(api, "How accurate is it?", chat_id="does-not-exist", fast=True)
    assert events[0]["new"] and events[0]["id"] != "does-not-exist"


def test_chat_requires_a_key(api):
    r = api.post("/api/chat", json={"question": "hi"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "missing_key"


def test_suggestions_are_generated_once_per_document_set(api):
    first = api.get("/api/suggestions", headers={"x-api-key": "test-key"}).json()
    assert first["source"] == "llm" and len(first["questions"]) == 3
    again = api.get("/api/suggestions", headers={"x-api-key": "test-key"}).json()
    assert again == {"questions": first["questions"], "source": "cache"}
    assert sum(1 for kind, _ in api.llm.calls if kind == "suggest") == 1


def test_suggestions_without_a_key_use_file_names(api):
    r = api.get("/api/suggestions").json()
    assert r["source"] == "fallback" and len(r["questions"]) == 3
    assert any("OrbitalNet" in q for q in r["questions"])
