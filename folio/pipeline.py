"""The answer pipeline: rewrite → retrieve → stream answer → evidence → claim check / conflicts.

`run()` is a generator of plain-dict events that the server sends as SSE. Every
step after the answer is optional: if it fails, the answer still stands and the
UI simply doesn't show that extra.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator

import openai
from pydantic import BaseModel, Field

from . import llm as llm_mod
from .chunking import split_sentences
from .config import AUDIT_LOG, LLM_BASE_URL, LLM_FAST_MODEL, LLM_MODEL
from .index import KnowledgeBase, tokenize
from .retrieval import Passage, Selection, confidence, detect_scope, score_level, select_passages

# --- Errors ---------------------------------------------------------------------------


def provider_error(e: Exception) -> dict:
    """A friendly {code, message} for a provider failure. Never includes the key."""
    if isinstance(e, openai.AuthenticationError):
        return {"code": "bad_key", "message": "The API key was rejected. Check it in Advanced settings."}
    if isinstance(e, openai.PermissionDeniedError):
        return {"code": "forbidden", "message": "This key doesn't have access to that model."}
    if isinstance(e, openai.NotFoundError):
        return {"code": "bad_model", "message": "That model isn't available. Pick another one in Settings."}
    if isinstance(e, openai.RateLimitError):
        return {"code": "rate_limited", "message": "The model provider's rate limit was reached. Wait a minute, or turn on Fast mode."}
    if isinstance(e, openai.APIConnectionError):
        return {"code": "unreachable", "message": "Couldn't reach the model provider. Check your connection."}
    if isinstance(e, openai.BadRequestError):
        return {"code": "bad_request", "message": "The provider rejected the request. Try a different model."}
    if isinstance(e, openai.APIStatusError):
        return {"code": "provider_error", "message": f"The provider returned an error ({e.status_code}). Try again."}
    return {"code": "server_error", "message": "Something went wrong while answering."}


def _model_kwargs(model: str, light: bool = False) -> dict:
    """Provider-specific knobs. gpt-oss spends tokens on hidden reasoning; keep helper calls cheap."""
    if light and model.startswith("openai/gpt-oss") and "groq.com" in LLM_BASE_URL:
        return {"extra_body": {"reasoning_effort": "low"}}
    return {}


# --- Prompts --------------------------------------------------------------------------

COVERAGE_MARK = "%%COVERAGE%%"

ANSWER_SYSTEM = f"""You are Folio, a careful document analyst. You answer using ONLY the numbered passages from the user's documents.

Rules:
- Use only facts stated in the passages. No outside knowledge, no guesses.
- Cite every factual sentence with the passage number(s) in square brackets at the end of the sentence, e.g. "Revenue grew 12% in Q3 [3]." or "... [2][5]". Only cite numbers that exist.
- Never give generic refusals such as "I can't review a CV" or "As an AI, I cannot...". When asked to check, review, summarize or assess a document, do it from the passages: say what it contains and what stands out, with citations.
- If the passages don't contain something the question asks, say plainly which part the documents don't cover, and answer the parts they do cover.
- If passages give different figures for what looks like the same thing, report both with their citations and say what each refers to.
- Be concise. Use Markdown: short paragraphs, bullet lists, **bold** for key figures. No list of sources at the end.
- Earlier conversation only tells you what the user means; it is not a source of facts.

After the answer, write a line containing only {COVERAGE_MARK} and then one line of JSON:
{{"covered": ["<what the passages answered, a few words each>"], "not_covered": ["<parts of the question the passages don't answer>"]}}
Use an empty list for not_covered when everything was answered."""


def _passages_block(passages: list[Passage]) -> str:
    return "\n\n".join(
        f"[{n}] {p.chunk.label}\n{p.chunk.text}" for n, p in enumerate(passages, start=1)
    )


def _history_block(turns: list[dict], max_answer: int = 700) -> list[dict]:
    messages = []
    for t in turns[-2:]:
        messages.append({"role": "user", "content": t["q"]})
        messages.append({"role": "assistant", "content": t["a"][:max_answer]})
    return messages


# --- Query rewriting -------------------------------------------------------------------


def rewrite_query(llm, model: str, question: str, turns: list[dict]) -> str:
    """Turn a follow-up ("what about its limitations?") into a standalone search query."""
    if not turns:
        return question
    transcript = "\n".join(f"User: {t['q']}\nAssistant: {t['a'][:400]}" for t in turns[-3:])
    use = LLM_FAST_MODEL or model
    try:
        response = llm.chat.completions.create(
            model=use,
            messages=[
                {"role": "system", "content": (
                    "Rewrite the user's latest message as a standalone question for searching their documents. "
                    "Resolve pronouns and references (it, that paper, the second one) using the conversation, and "
                    "keep any file names. If the message continues the same topic without naming it (for example "
                    "'put every metric in a table'), add the document or subject being discussed. "
                    "Return only the rewritten question, with no quotes or explanation. "
                    "If it is already standalone, return it unchanged."
                )},
                {"role": "user", "content": f"Conversation:\n{transcript}\n\nLatest message: {question}"},
            ],
            temperature=0, max_tokens=400, **_model_kwargs(use, light=True),
        )
    except openai.AuthenticationError:
        raise
    except Exception:
        return question  # an optimisation: never fail the request over it
    text = llm_mod.strip_think(response.choices[0].message.content or "").strip().strip("\"'“”").strip()
    text = re.sub(r"^(rewritten( question)?|query)\s*:\s*", "", text, flags=re.IGNORECASE)
    return text if 0 < len(text) <= 400 else question


# --- Streaming helpers -------------------------------------------------------------------


class ThinkFilter:
    """Drops a leading <think>...</think> block that some models emit."""

    def __init__(self):
        self.buf, self.decided, self.thinking = "", False, False

    def feed(self, piece: str) -> str:
        if self.decided and not self.thinking:
            return piece
        self.buf += piece
        if not self.decided:
            head = self.buf.lstrip()
            if len(head) < 7 and "<think>".startswith(head):
                return ""
            self.decided, self.thinking = True, head.startswith("<think>")
        if self.thinking:
            if "</think>" not in self.buf:
                return ""
            self.buf, self.thinking = self.buf.split("</think>", 1)[1].lstrip(), False
        out, self.buf = self.buf, ""
        return out

    def finish(self) -> str:
        out = "" if self.thinking else self.buf
        self.buf = ""
        return out


class TrailerSplitter:
    """Passes answer text through and captures everything after the coverage marker."""

    def __init__(self, mark: str = COVERAGE_MARK):
        self.mark, self.buf, self.trailer = mark, "", None

    def feed(self, piece: str) -> str:
        if self.trailer is not None:
            self.trailer += piece
            return ""
        self.buf += piece
        idx = self.buf.find(self.mark)
        if idx != -1:
            out, self.trailer, self.buf = self.buf[:idx], self.buf[idx + len(self.mark):], ""
            return out
        keep = 0  # hold back a possible partial marker at the end
        for n in range(min(len(self.mark) - 1, len(self.buf)), 0, -1):
            if self.mark.startswith(self.buf[-n:]):
                keep = n
                break
        out, self.buf = self.buf[: len(self.buf) - keep], self.buf[len(self.buf) - keep:]
        return out

    def finish(self) -> str:
        out, self.buf = self.buf, ""
        return out


def parse_coverage(trailer: str | None, answer: str) -> tuple[dict | None, str]:
    """Read the coverage JSON. Falls back to a stray JSON object at the end of the answer."""
    data = llm_mod.parse_json(trailer) if trailer else None
    if data is None:
        m = re.search(r"\{[^{}]*\"covered\"[^{}]*\}\s*$", answer)
        if m:
            data = llm_mod.parse_json(m.group(0))
            answer = answer[: m.start()]
    answer = re.sub(r"(\s*(\*\*|%%|---|```))+\s*$", "", answer).strip()
    if not isinstance(data, dict):
        return None, answer
    covered = [str(x).strip() for x in data.get("covered", []) if str(x).strip()][:8]
    not_covered = [str(x).strip() for x in data.get("not_covered", []) if str(x).strip()][:8]
    return {"covered": covered, "not_covered": not_covered}, answer


# --- Citations and evidence ----------------------------------------------------------------

CITE_RE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")


def citation_numbers(group: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*,\s*", group):
        if re.search(r"[–-]", part):
            a, b = (int(x) for x in re.split(r"\s*[–-]\s*", part)[:2])
            out.extend(range(min(a, b), max(a, b) + 1) if b - a < 20 else [a, b])
        elif part.strip().isdigit():
            out.append(int(part))
    return out


def _plain(text: str) -> str:
    return re.sub(r"[*_`#>|]", "", CITE_RE.sub("", text)).strip()


def cited_sentences(answer: str) -> list[tuple[str, list[int]]]:
    """Each citation marker in reading order, with the answer sentence it closes."""
    out = []
    for line in answer.split("\n"):
        last = 0
        for m in CITE_RE.finditer(line):
            # The sentence is the text since the previous citation or sentence end.
            before = line[last:m.start()]
            seg = re.split(r"(?<=[.!?])\s+(?=[A-Z*])", before.strip())
            sentence = _plain(seg[-1] if seg else before) or _plain(before)
            out.append((sentence, citation_numbers(m.group(1))))
            last = m.end()
    return out


def _norm_tokens(text: str) -> list[str]:
    return tokenize(re.sub(r"(\d)\s+([.%])", r"\1\2", text))


def best_sentence(chunk_text: str, claim: str) -> tuple[int, int] | None:
    """Span of the chunk sentence that best supports `claim` (shared words, numbers count double)."""
    claim_tokens = set(_norm_tokens(claim))
    if not claim_tokens:
        return None
    best, best_score = None, 0.0
    pos = 0
    for sentence in split_sentences(chunk_text):
        start = chunk_text.find(sentence, pos)
        if start == -1:
            continue
        pos = start + len(sentence)
        toks = set(_norm_tokens(sentence))
        if not toks:
            continue
        shared = claim_tokens & toks
        score = sum(2.0 if any(c.isdigit() for c in t) else 1.0 for t in shared) / (len(claim_tokens) ** 0.5 * len(toks) ** 0.25)
        if score > best_score:
            best, best_score = (start, start + len(sentence)), score
    return best if best_score >= 0.6 else None


def locate_quote(chunk_text: str, quote: str) -> tuple[int, int, bool] | None:
    """Find `quote` in the chunk: exact, then case/space-insensitive, then fuzzy. Returns (start, end, exact)."""
    q = re.sub(r"\s+", " ", (quote or "").strip().strip("\"'“”"))
    if len(q) < 8:
        return None
    i = chunk_text.find(q)
    if i != -1:
        return i, i + len(q), True
    lower = chunk_text.lower()
    if len(lower) == len(chunk_text):
        i = lower.find(q.lower())
        if i != -1:
            return i, i + len(q), True
    # Fuzzy: compare against each sentence and each pair of adjacent sentences.
    spans: list[tuple[int, int]] = []
    pos = 0
    for s in split_sentences(chunk_text):
        start = chunk_text.find(s, pos)
        if start == -1:
            continue
        spans.append((start, start + len(s)))
        pos = start + len(s)
    candidates = spans + [(a[0], b[1]) for a, b in zip(spans, spans[1:])]
    best, best_ratio = None, 0.0
    ql = q.lower()
    for a, b in candidates:
        ratio = difflib.SequenceMatcher(None, ql, chunk_text[a:b].lower(), autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = (a, b), ratio
    return (best[0], best[1], False) if best and best_ratio >= 0.72 else None


# --- Claim check + conflicts ----------------------------------------------------------------------


class ClaimItem(BaseModel):
    claim: str
    passages: list[int] = Field(default_factory=list)
    quote: str = ""
    supported: bool


class Side(BaseModel):
    value: str
    label: str = ""
    passage: int


class ConflictItem(BaseModel):
    topic: str
    a: Side
    b: Side
    explanation: str
    contradiction: bool = True


class CheckResult(BaseModel):
    claims: list[ClaimItem] = Field(default_factory=list, max_length=12)
    conflicts: list[ConflictItem] = Field(default_factory=list, max_length=5)


def _has_figures(passages: list[Passage]) -> bool:
    return sum(1 for p in passages if re.search(r"\d+(\.\d+)?\s*%|\d+\.\d+", p.chunk.text)) >= 2


def check_answer(llm, model: str, answer: str, passages: list[Passage], conflicts: bool) -> CheckResult | None:
    task = (
        "1. Split the ANSWER into its factual claims (at most 8; one per factual sentence or bullet). "
        "Skip sentences that only say the documents don't cover something. For each claim give: "
        "\"claim\" (short paraphrase), \"passages\" (numbers of the passages that support it, usually the ones it cites), "
        "\"quote\" (the exact sentence from that passage that supports it, copied verbatim; \"\" if none), "
        "\"supported\" (true only if the passages actually state it).\n"
    )
    if conflicts:
        task += (
            "2. Conflicts: find passages that give different values for what looks like the same quantity "
            "(for example two different improvement percentages for the same model). For each give \"topic\", "
            "\"a\" and \"b\" as {\"value\", \"label\" (what that value is measured against or refers to), \"passage\"}, "
            "\"explanation\" (one sentence), and \"contradiction\" (false when the difference is explained, e.g. "
            "different baselines, subsets, dates or units). At most 3. Use [] if there are none.\n"
        )
    shape = '{"claims": [{"claim": "", "passages": [1], "quote": "", "supported": true}], "conflicts": []}'
    if conflicts:
        shape = ('{"claims": [...], "conflicts": [{"topic": "", "a": {"value": "", "label": "", "passage": 1}, '
                 '"b": {"value": "", "label": "", "passage": 2}, "explanation": "", "contradiction": false}]}')
    messages = [
        {"role": "system", "content": "You verify answers against source passages. Be strict and literal. Reply with JSON only."},
        {"role": "user", "content": f"PASSAGES:\n{_passages_block(passages)}\n\nANSWER:\n{answer}\n\nTASK:\n{task}\nReturn JSON shaped like: {shape}"},
    ]
    return llm_mod.complete_json(llm, model, messages, CheckResult, max_tokens=2500, extra=_model_kwargs(model, light=True))


# --- Extraction -----------------------------------------------------------------------------------------


class ExtractRow(BaseModel):
    values: list[str]
    passage: int
    quote: str = ""


class Extraction(BaseModel):
    title: str = ""
    columns: list[str] = Field(min_length=1, max_length=8)
    rows: list[ExtractRow] = Field(default_factory=list, max_length=60)
    summary: str = ""


def extract_table(llm, model: str, question: str, passages: list[Passage]) -> Extraction | None:
    messages = [
        {"role": "system", "content": "You extract structured data from document passages. Copy values exactly. Reply with JSON only."},
        {"role": "user", "content": (
            f"PASSAGES:\n{_passages_block(passages)}\n\nREQUEST: {question}\n\n"
            "Build a table with every item the request asks for that appears in the passages. Choose 3-5 short "
            "columns (for example Metric, Value, Model, Context). Each row comes from ONE passage: give its "
            "\"passage\" number and \"quote\" (the exact sentence or table fragment containing the values, copied "
            "verbatim). Copy values exactly as written, including units and ± ranges. Don't invent or compute rows. "
            "Skip duplicates of the same value for the same thing. \"summary\" is one sentence describing what the "
            "table shows, with [n] citations.\n"
            'Return JSON shaped like: {"title": "", "columns": ["Metric", "Value"], "rows": '
            '[{"values": ["Revenue", "4.2M"], "passage": 1, "quote": "..."}], "summary": ""}'
        )},
    ]
    result = llm_mod.complete_json(llm, model, messages, Extraction, max_tokens=3500, extra=_model_kwargs(model, light=True))
    if result is None or not result.rows:
        return None
    return result


def _squash(text: str) -> str:
    """Normalise for verbatim checks: case, whitespace, and PDF spacing like "92 .34" or "5 .85%"."""
    t = re.sub(r"\s+", " ", text.lower()).strip()
    return re.sub(r"\s+([.,%])", r"\1", t)


def value_verbatim(value: str, text: str) -> bool | None:
    """True/False for values that contain a figure; None for labels we don't check."""
    if not re.search(r"\d", value or ""):
        return None
    return _squash(value) in _squash(text)


# --- Audit ------------------------------------------------------------------------------------------------

_audit_lock = threading.Lock()


def audit(record: dict) -> None:
    """Append one JSON line to the audit log. Must never contain the API key."""
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _audit_lock, AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# --- The pipeline ------------------------------------------------------------------------------------------


@dataclass
class Request:
    question: str
    model: str = ""
    k: int = 4
    fast: bool = False
    doc: str | None = None  # explicit scope from the document picker
    chat_id: str = ""
    api_key: str = field(default="", repr=False)


def _passage_info(n: int, p: Passage) -> dict:
    c = p.chunk
    return {"n": n, "id": c.id, "doc": c.doc, "doc_id": c.doc_id, "kind": c.kind, "loc": c.loc,
            "location": c.location, "label": c.label, "text": c.text, "score": round(p.score, 3),
            "level": score_level(p.score)}


def run(kb: KnowledgeBase, kb_lock: threading.Lock, req: Request, history: list[dict] | None = None,
        llm=None) -> Iterator[dict]:
    """Answer one question as a stream of events. See README "API" for the event list.

    `history` is the chat's recent turns ([{"q", "a"}], oldest first), used to
    rewrite follow-ups. Saving the turn is the caller's job.
    """
    started = time.time()
    llm = llm or llm_mod.client(req.api_key)
    model = req.model or LLM_MODEL
    turns = list(history or [])
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "chat": req.chat_id, "question": req.question,
              "model": model, "fast": req.fast, "k": req.k}

    def step(sid: str, label: str, state: str = "active") -> dict:
        return {"type": "step", "id": sid, "label": label, "state": state}

    try:
        # 1. Rewrite follow-ups into standalone queries.
        query = req.question
        if turns:
            yield step("rewrite", "Reading the conversation…")
            query = rewrite_query(llm, model, req.question, turns)
            yield step("rewrite", "Understood the follow-up", "done")
        record["query"] = query

        # 2. Scope and retrieve.
        with kb_lock:
            docs = list(kb.docs.values())
            if not docs:
                yield {"type": "error", "code": "no_docs", "message": "Add a document first, then ask about it."}
                return
            scope = [req.doc] if req.doc and req.doc in kb.docs else detect_scope(f"{req.question} {query}", docs)
            names = [kb.docs[d].name for d in scope]
            label = f"Reading {names[0]}…" if len(names) == 1 else f"Searching {len(scope) or len(docs)} documents…"
            yield step("search", label)
            selection: Selection = select_passages(kb, query, f"{req.question} {query}", k=req.k, scope=scope)
        passages = selection.passages
        conf = confidence(selection)
        record.update(scope=names, mode=selection.mode, chunk_ids=[p.chunk.id for p in passages], confidence=conf)
        yield step("search", f"Found {len(passages)} passages" + (f" in {names[0]}" if len(names) == 1 else ""), "done")
        yield {"type": "sources", "mode": selection.mode, "scope": names, "query": query if query != req.question else "",
               "confidence": conf, "passages": [_passage_info(n, p) for n, p in enumerate(passages, 1)]}

        # 3a. Extraction mode: a cited table.
        if selection.mode == "extract":
            yield step("extract", "Extracting values…")
            table = extract_table(llm, model, req.question, passages)
            if table is not None:
                rows = []
                for r in table.rows:
                    if not 1 <= r.passage <= len(passages):
                        continue
                    chunk = passages[r.passage - 1].chunk
                    span = locate_quote(chunk.text, r.quote)
                    rows.append({
                        "cells": [{"value": v, "verbatim": value_verbatim(v, chunk.text)} for v in r.values[: len(table.columns)]],
                        "passage": r.passage, "chunk_id": chunk.id, "doc": chunk.doc, "location": chunk.location,
                        "quote": chunk.text[span[0]:span[1]] if span else r.quote,
                        "span": [span[0], span[1]] if span else None,
                    })
                summary = table.summary.strip() or f"Found {len(rows)} values in the documents."
                yield step("extract", f"Extracted {len(rows)} rows", "done")
                yield {"type": "delta", "text": summary}
                yield {"type": "table", "title": table.title, "columns": table.columns[:8], "rows": rows}
                yield {"type": "answer", "text": summary, "citations": _citations(summary, passages)}
                flagged = sum(1 for r in rows for c in r["cells"] if c["verbatim"] is False)
                record.update(table_rows=len(rows), unverified_values=flagged)
                yield {"type": "done", "ms": int((time.time() - started) * 1000)}
                return
            yield step("extract", "Couldn't build a table; answering in text", "done")

        # 3b. Stream the answer.
        yield step("write", "Writing the answer…")
        messages = [{"role": "system", "content": ANSWER_SYSTEM}, *_history_block(turns),
                    {"role": "user", "content": f"PASSAGES:\n\n{_passages_block(passages)}\n\nQUESTION: {req.question}"}]
        stream = llm.chat.completions.create(model=model, messages=messages, temperature=0.1, stream=True,
                                             **_model_kwargs(model))
        think, splitter, parts = ThinkFilter(), TrailerSplitter(), []
        try:
            for event in stream:
                if not event.choices:
                    continue
                piece = event.choices[0].delta.content or ""
                out = splitter.feed(think.feed(piece)) if piece else ""
                if out:
                    parts.append(out)
                    yield {"type": "delta", "text": out}
            tail = splitter.feed(think.finish()) + splitter.finish()
            if tail:
                parts.append(tail)
                yield {"type": "delta", "text": tail}
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        coverage, answer = parse_coverage(splitter.trailer, "".join(parts))
        yield step("write", "Answer written", "done")
        yield {"type": "answer", "text": answer, "citations": _citations(answer, passages)}
        if coverage and coverage["not_covered"]:
            state = "partial" if coverage["covered"] else "none"
            yield {"type": "coverage", "state": state, **coverage}
            record["coverage"] = {"state": state, **coverage}

        # 4. Claim check + conflicts (skipped in Fast mode).
        check_summary = {"ran": False}
        if req.fast:
            check_summary["reason"] = "fast_mode"
        elif not CITE_RE.search(answer):
            check_summary["reason"] = "no_citations"
        else:
            want_conflicts = _has_figures(passages)
            yield step("check", "Checking claims…")
            result = check_answer(llm, model, answer, passages, conflicts=want_conflicts)
            if result is None or not result.claims:
                yield step("check", "Claim check unavailable", "done")
                check_summary["reason"] = "failed"
            else:
                claims = []
                for c in result.claims:
                    nums = [n for n in c.passages if 1 <= n <= len(passages)]
                    ev = None
                    # The cited passages first, then any other passage the quote might come from.
                    for n in nums + [m for m in range(1, len(passages) + 1) if m not in nums]:
                        span = locate_quote(passages[n - 1].chunk.text, c.quote)
                        if span:
                            ev = {"n": n, "start": span[0], "end": span[1], "exact": span[2]}
                            break
                    claims.append({"claim": c.claim, "passages": nums, "supported": bool(c.supported and nums), "evidence": ev})
                supported = sum(1 for c in claims if c["supported"])
                check_summary = {"ran": True, "supported": supported, "total": len(claims),
                                 "unsupported": [c["claim"] for c in claims if not c["supported"]]}
                yield step("check", f"{supported} of {len(claims)} claims supported", "done")
                yield {"type": "check", "ran": True, "supported": supported, "total": len(claims), "claims": claims}

                conflicts = []
                for cf in result.conflicts if want_conflicts else []:
                    if not (1 <= cf.a.passage <= len(passages) and 1 <= cf.b.passage <= len(passages)):
                        continue
                    if cf.a.value.strip() == cf.b.value.strip() or cf.a.passage == cf.b.passage and cf.a.label == cf.b.label:
                        continue
                    sides = []
                    for side in (cf.a, cf.b):
                        ch = passages[side.passage - 1].chunk
                        sides.append({"value": side.value, "label": side.label, "passage": side.passage,
                                      "doc": ch.doc, "location": ch.location, "chunk_id": ch.id,
                                      "verbatim": value_verbatim(side.value, ch.text)})
                    conflicts.append({"topic": cf.topic, "sides": sides, "explanation": cf.explanation,
                                      "contradiction": cf.contradiction})
                if conflicts:
                    yield {"type": "conflicts", "items": conflicts}
                record["conflicts"] = len(conflicts)
        record["claim_check"] = check_summary
        yield {"type": "done", "ms": int((time.time() - started) * 1000)}

    except openai.APIError as e:
        err = provider_error(e)
        record["error"] = err["code"]
        yield {"type": "error", **err}
    except GeneratorExit:
        record["error"] = "cancelled"
        raise
    except Exception:
        record["error"] = "server_error"
        yield {"type": "error", "code": "server_error", "message": "Something went wrong while answering."}
    finally:
        record["ms"] = int((time.time() - started) * 1000)
        audit(record)


def _citations(answer: str, passages: list[Passage]) -> list[dict]:
    """For each citation marker, in order: the passage it points to and the sentence that supports it."""
    out = []
    for sentence, numbers in cited_sentences(answer):
        for n in numbers:
            if not 1 <= n <= len(passages):
                out.append({"n": n, "valid": False})
                continue
            span = best_sentence(passages[n - 1].chunk.text, sentence)
            out.append({"n": n, "valid": True, "start": span[0] if span else None, "end": span[1] if span else None})
    return out


# --- Titles and suggested questions -------------------------------------------------


def _cheap_model(model: str) -> str:
    """The cheapest sensible model for small helper calls."""
    if LLM_FAST_MODEL:
        return LLM_FAST_MODEL
    if "groq.com" in LLM_BASE_URL:
        return "llama-3.1-8b-instant"
    return model or LLM_MODEL


def generate_title(llm, model: str, question: str, answer: str) -> str | None:
    """A 3-6 word chat title from the first exchange, or None (caller keeps the fallback)."""
    from .chats import clean_llm_title
    try:
        response = llm.chat.completions.create(
            model=_cheap_model(model),
            messages=[
                {"role": "system", "content": "Write a short title (3 to 6 words) for this chat. Plain text, no quotes, no trailing period."},
                {"role": "user", "content": f"Question: {question}\n\nAnswer: {answer[:600]}"},
            ],
            temperature=0.2, max_tokens=30,
        )
    except Exception:
        return None
    return clean_llm_title(response.choices[0].message.content or "")


class Suggestions(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=6)


def suggest_questions(llm, model: str, previews: list[tuple[str, str]]) -> list[str] | None:
    """Three questions a reader could ask about these documents. previews = [(name, opening text)]."""
    listing = "\n\n".join(f"Document: {name}\n{text[:700]}" for name, text in previews[:5])
    result = llm_mod.complete_json(llm, _cheap_model(model), [
        {"role": "system", "content": "You suggest questions a reader could ask about their documents. Reply with JSON only."},
        {"role": "user", "content": (
            f"{listing}\n\nWrite 3 short, specific questions (under 60 characters each) that these documents can "
            "answer. Mix them across the documents and refer to documents by a short name (\"the CV\", \"the Q3 "
            "report\"), not the file name. Return JSON: {\"questions\": [\"...\", \"...\", \"...\"]}"
        )},
    ], Suggestions, max_tokens=300)
    if result is None:
        return None
    out = []
    for q in result.questions:
        q = re.sub(r"\s+", " ", q).strip().strip("\"'")
        if 8 <= len(q) <= 80 and q not in out:
            out.append(q)
    return out[:3] or None
