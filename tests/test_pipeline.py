import json
import threading

try:  # openai 3.x is built on httpx2; older versions on httpx
    import httpx2 as httpx
except ImportError:
    import httpx
import openai
import pytest

from folio import llm as llm_mod
from folio.pipeline import (CheckResult, Request, ThinkFilter, TrailerSplitter, cited_sentences,
                            locate_quote, parse_coverage, run, value_verbatim)

# --- JSON parsing and fallbacks ----------------------------------------------------------------


@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Sure! Here it is: {"a": 1} Hope that helps.',
    '<think>{"draft": true}</think>{"a": 1}',
    'noise {not json} then {"a": 1}',
])
def test_parse_json_tolerates_wrappers(text):
    assert llm_mod.parse_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json here", '{"a": ', "[1, 2]"])
def test_parse_json_returns_none_on_garbage(text):
    assert llm_mod.parse_json(text) is None


def _bad_request():
    req = httpx.Request("POST", "http://x")
    return openai.BadRequestError("json_validate_failed", response=httpx.Response(400, request=req), body=None)


def _rate_limited():
    req = httpx.Request("POST", "http://x")
    return openai.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)


VALID_CHECK = json.dumps({"claims": [{"claim": "x", "passages": [1], "quote": "", "supported": True}]})


def test_complete_json_retries_without_json_mode(fake_llm):
    llm = fake_llm(check=[_bad_request(), VALID_CHECK])
    result = llm_mod.complete_json(llm, "m", [{"role": "system", "content": "verify answers"}], CheckResult)
    assert result is not None and result.claims[0].supported
    assert "response_format" in llm.calls[0][1] and "response_format" not in llm.calls[1][1]


def test_complete_json_rejects_schema_violations(fake_llm):
    llm = fake_llm(check=['{"claims": [{"claim": "x"}]}', '{"claims": "nope"}'])  # missing required field, wrong type
    assert llm_mod.complete_json(llm, "m", [{"role": "system", "content": "verify answers"}], CheckResult) is None


def test_complete_json_gives_up_quietly_on_rate_limit(fake_llm):
    llm = fake_llm(check=[_rate_limited()])
    assert llm_mod.complete_json(llm, "m", [{"role": "system", "content": "verify answers"}], CheckResult) is None
    assert len(llm.calls) == 1


def test_model_catalogue_filters_and_labels():
    ids = ["whisper-large-v3", "playai-tts", "allam-2-7b", "meta-llama/llama-guard-4-12b", "groq/compound",
           "llama-3.1-8b-instant", "openai/gpt-oss-120b", "llama-3.3-70b-versatile", "some/new-model"]
    options, default = llm_mod.chat_models(ids)
    assert [o["id"] for o in options] == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "some/new-model"]
    assert (options[0]["label"], options[0]["sub"]) == ("Best quality", "GPT-OSS 120B")
    assert (options[2]["label"], options[2]["sub"]) == ("Fastest", "Llama 3.1 8B")
    assert (options[-1]["label"], options[-1]["sub"]) == ("New Model", "some/new-model")
    assert default in {o["id"] for o in options}


def test_default_model_exists_on_the_account():
    options, default = llm_mod.chat_models(["llama-3.1-8b-instant"])
    assert default == "llama-3.1-8b-instant"


# --- Streaming helpers ------------------------------------------------------------------------------


def test_trailer_splitter_hides_coverage_even_when_split_across_chunks():
    s, out = TrailerSplitter(), ""
    for piece in ["The answer [1].", "\n%%COV", "ERAGE%%\n", '{"covered": ["a"], "not_covered": ["b"]}']:
        out += s.feed(piece)
    out += s.finish()
    assert out.strip() == "The answer [1]."
    cov, answer = parse_coverage(s.trailer, out)
    assert cov == {"covered": ["a"], "not_covered": ["b"]} and answer == "The answer [1]."


def test_coverage_missing_or_broken_is_tolerated():
    assert parse_coverage(None, "Plain answer.") == (None, "Plain answer.")
    assert parse_coverage("{oops", "Plain answer.")[0] is None


def test_think_filter_drops_reasoning():
    f, out = ThinkFilter(), ""
    for piece in ["<thi", "nk>secret plan</th", "ink>Visible ", "answer."]:
        out += f.feed(piece)
    assert out + f.finish() == "Visible answer."


# --- Citations and evidence ------------------------------------------------------------------------


def test_cited_sentences_in_reading_order():
    answer = "Accuracy was **90.20%** [1]. It beat CNNs by 6.10% [2, 3].\n- Early-stage: 94.10% [1]"
    got = cited_sentences(answer)
    assert [n for _, ns in got for n in ns] == [1, 2, 3, 1]
    assert got[0][0] == "Accuracy was 90.20%"  # the full stop follows the marker


def test_locate_quote_exact_case_insensitive_fuzzy_and_missing():
    text = "The model was trained for 100 epochs. It reached 90.20% accuracy on FieldScan. Training used focal loss."
    s, e, exact = locate_quote(text, "It reached 90.20% accuracy on FieldScan.")
    assert exact and text[s:e] == "It reached 90.20% accuracy on FieldScan."
    assert locate_quote(text, "it reached 90.20% ACCURACY on fieldscan.")[2] is True
    s, e, exact = locate_quote(text, "It reached about 90.20 % accuracy on the FieldScan set.")
    assert not exact and "90.20%" in text[s:e]
    assert locate_quote(text, "Completely unrelated sentence about bread baking.") is None


def test_value_verbatim_handles_pdf_spacing():
    text = "Ours 92 .38 ± 0.43% and recall 94.61%."
    assert value_verbatim("92.38 ± 0.43%", text) is True
    assert value_verbatim("94.61%", text) is True
    assert value_verbatim("99.99%", text) is False
    assert value_verbatim("Accuracy", text) is None  # labels aren't checked


# --- The pipeline end to end (mocked LLM) ----------------------------------------------------------

ANSWER = ("OrbitalNet reached **90.20%** overall and 94.10% early-stage accuracy [1].\n"
          "%%COVERAGE%%\n{\"covered\": [\"accuracy\"], \"not_covered\": [\"external validation\"]}")


def _events(kb, llm, **req):
    return list(run(kb, threading.Lock(), Request(**{"question": "How accurate is OrbitalNet?", **req}), llm=llm))


def test_pipeline_streams_answer_then_checks(kb, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("folio.pipeline.AUDIT_LOG", tmp_path / "audit.jsonl")
    check = json.dumps({"claims": [{"claim": "90.20% overall", "passages": [1],
                                    "quote": "OrbitalNet detects early crop disease with 94.10% accuracy and overall performance 90.20%.",
                                    "supported": True}],
                        "conflicts": [{"topic": "improvement", "a": {"value": "+6.10%", "label": "vs CNNs", "passage": 1},
                                       "b": {"value": "3.70%", "label": "vs LeafFormer", "passage": 2},
                                       "explanation": "Different baselines.", "contradiction": False}]})
    llm = fake_llm(answer=ANSWER, check=check)
    events = _events(kb, llm)
    types = [e["type"] for e in events]
    assert types[-1] == "done" and "error" not in types
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "%%COVERAGE" not in text and "90.20%" in text
    assert next(e for e in events if e["type"] == "coverage")["state"] == "partial"
    chk = next(e for e in events if e["type"] == "check")
    assert chk["ran"] and chk["supported"] == chk["total"] == 1 and chk["claims"][0]["evidence"]["exact"]
    conflict = next(e for e in events if e["type"] == "conflicts")["items"][0]
    assert conflict["contradiction"] is False and conflict["sides"][0]["location"].startswith("page")
    audit = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert audit["claim_check"]["ran"] and audit["chunk_ids"] and "api_key" not in json.dumps(audit)


def test_failed_check_never_shows_a_badge(kb, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("folio.pipeline.AUDIT_LOG", tmp_path / "audit.jsonl")
    llm = fake_llm(answer=ANSWER, check=["not json at all", "still not json"])
    events = _events(kb, llm)
    assert not any(e["type"] == "check" for e in events), "no check event => UI shows no badge"
    assert any(e["type"] == "answer" for e in events) and events[-1]["type"] == "done"


def test_fast_mode_makes_one_call(kb, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("folio.pipeline.AUDIT_LOG", tmp_path / "audit.jsonl")
    llm = fake_llm(answer=ANSWER)
    _events(kb, llm, fast=True)
    assert [kind for kind, _ in llm.calls] == ["answer"]


def test_follow_up_is_rewritten_with_history(kb, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("folio.pipeline.AUDIT_LOG", tmp_path / "audit.jsonl")
    history = [{"q": "Tell me about OrbitalNet", "a": "It detects crop disease [1]."}]
    llm = fake_llm(rewrite="What are OrbitalNet's limitations?", answer=ANSWER)
    events = list(run(kb, threading.Lock(), Request("what about its limitations?", fast=True), history, llm=llm))
    sources = next(e for e in events if e["type"] == "sources")
    assert sources["query"] == "What are OrbitalNet's limitations?"
    assert sources["scope"] == ["OrbitalNet.pdf"]


def test_extraction_marks_values_not_in_the_text(kb, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("folio.pipeline.AUDIT_LOG", tmp_path / "audit.jsonl")
    table = {"title": "Metrics", "columns": ["Metric", "Value"], "summary": "Two values [1].", "rows": [
        {"values": ["Overall accuracy", "90.20%"], "passage": 1, "quote": "overall performance 90.20%"},
        {"values": ["Made up", "99.99%"], "passage": 1, "quote": "nothing"},
    ]}
    llm = fake_llm(extract=json.dumps(table))
    events = _events(kb, llm, question="Put every reported metric for OrbitalNet in a table")
    rows = next(e for e in events if e["type"] == "table")["rows"]
    assert rows[0]["cells"][1]["verbatim"] is True and rows[0]["span"]
    assert rows[1]["cells"][1]["verbatim"] is False


def test_provider_errors_become_friendly_events(kb, fake_llm, tmp_path, monkeypatch):
    monkeypatch.setattr("folio.pipeline.AUDIT_LOG", tmp_path / "audit.jsonl")
    llm = fake_llm(answer=_rate_limited())
    events = _events(kb, llm)
    err = events[-1]
    assert err["type"] == "error" and err["code"] == "rate_limited" and "Fast mode" in err["message"]
