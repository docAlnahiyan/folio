"""Choosing passages for a question: hybrid ranking, document scoping, whole-document spreads."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath

import numpy as np

from .index import KnowledgeBase
from .types import Chunk, Doc

RRF_K = 60
MAX_CONTEXT = 10  # passages sent to the model, whatever the mode


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: score(d) = sum over rankings of 1 / (k + rank), rank starting at 1."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# --- Intents ----------------------------------------------------------------
_WHOLE_DOC = re.compile(
    r"\b(summari[sz]e|summary|overview|tl;?dr|outline|key (points|takeaways|findings|ideas)|"
    r"main (points|ideas|findings|themes|contributions|argument)|what('s| is) in\b|"
    r"what (is|does|did) (the|this|that) [\w\s]{0,40}?(about|say|cover|propose|argue|find|show|claim|conclude|contribute)|"
    r"check\b|review\b|go through|walk me through|describe (the|this)|tell me about (the|this))",
    re.IGNORECASE,
)
_EXTRACT = re.compile(
    r"\b(table|tabulate|spreadsheet|as (a )?csv|list (all|every|each)|every (reported |single )?\w+|"
    r"all (the |of the )?(reported )?(metrics|figures|numbers|dates|deadlines|values|results|amounts|names|"
    r"percentages|scores|prices|costs)|extract)\b",
    re.IGNORECASE,
)


_FIGURE = re.compile(r"\d+(?:\s?\.\s?\d+)?\s?%|\b\d+\s?\.\s?\d+\b")
_FIGURES_WANTED = re.compile(r"\b(metric|figure|number|value|result|score|percentage|accuracy|amount|statistic)s?\b", re.IGNORECASE)


def is_whole_document(question: str) -> bool:
    return bool(_WHOLE_DOC.search(question))


def is_extraction(question: str) -> bool:
    return bool(_EXTRACT.search(question))


# --- Document scoping ---------------------------------------------------------
_GENERIC_NAME_WORDS = frozenset(
    "a an and the of in on for to with when where at by from vs via final draft copy new old v1 v2 v3 "
    "paper papers document documents doc docs file files pdf report version updated latest scan "
    "based using toward towards".split()
)
_CUES = re.compile(
    r"\b(paper|pdf|file|doc|docx|document|report|article|study|thesis|manuscript|cv|resume|résumé|"
    r"curriculum|deck|slides?|presentation|sheet|spreadsheet|workbook)\b",
    re.IGNORECASE,
)
_ALIASES = {"cv": {"resume", "résumé", "curriculum"}}


def _name_tokens(name: str) -> tuple[set[str], set[str]]:
    """(strong tokens, weak tokens) for a file name.

    Strong: the whole stem, or a camelCase / alphanumeric coinage like
    "OrbitalNet" or "cvJaneDoe": mentioning one names the file.
    Weak: ordinary words in the name ("leaks", "cv"): they name the file only
    when the question also refers to a document ("the leaks paper").
    """
    stem = PurePath(name).stem
    strong = {re.sub(r"[^a-z0-9]", "", stem.lower())}
    weak: set[str] = set()
    for word in re.findall(r"[A-Za-z0-9]+", stem):
        coined = bool(re.search(r"[a-z][A-Z]", word)) or (bool(re.search(r"\d", word)) and bool(re.search(r"[A-Za-z]", word)))
        if coined and len(word) >= 5:
            strong.add(word.lower())
        for piece in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", word):
            p = piece.lower()
            if len(p) >= 2 and p not in _GENERIC_NAME_WORDS and not p.isdigit():
                weak.add(p)
                weak |= _ALIASES.get(p, set())
    return {s for s in strong if len(s) >= 5}, weak


def detect_scope(question: str, docs: list[Doc]) -> list[str]:
    """Doc ids the question explicitly refers to by name, or [] for "all documents"."""
    if not docs:
        return []
    q = question.lower()
    q_words = set(re.findall(r"[a-z0-9]+|résumé", q))
    q_compact = re.sub(r"[^a-z0-9]", "", q)
    has_cue = bool(_CUES.search(question)) or is_whole_document(question)

    strong_hits, weak_scores = [], {}
    for doc in docs:
        strong, weak = _name_tokens(doc.name)
        if doc.name.lower() in q or any(s in q_words or s in q_compact for s in strong):
            strong_hits.append(doc.id)
            continue
        # A weak word only counts if no other document's name shares it.
        others = set().union(*(_name_tokens(d.name)[1] for d in docs if d.id != doc.id)) if len(docs) > 1 else set()
        matched = (weak - others) & q_words
        if matched:
            weak_scores[doc.id] = len(matched)

    if strong_hits:
        return strong_hits
    if has_cue and weak_scores:
        best = max(weak_scores.values())
        return [d for d, s in weak_scores.items() if s == best]
    return []


# --- Selection ------------------------------------------------------------------
@dataclass
class Passage:
    chunk: Chunk
    score: float  # cosine similarity to the query


@dataclass
class Selection:
    passages: list[Passage]
    mode: str                    # "search" | "whole" | "extract"
    scope: list[str] = field(default_factory=list)
    top_score: float = 0.0
    complete: bool = False       # every chunk of the scoped documents was included


def _spread(n_items: int, n: int) -> list[int]:
    """n evenly spaced positions over range(n_items), always including the first and last."""
    if n >= n_items:
        return list(range(n_items))
    if n <= 1:
        return [0]
    return sorted({round(i * (n_items - 1) / (n - 1)) for i in range(n)})


def select_passages(kb: KnowledgeBase, query: str, question: str, k: int = 4,
                    scope: list[str] | None = None) -> Selection:
    """Pick the passages to show the model.

    - search:  hybrid dense + BM25 ranking, fused with RRF, top k.
    - whole:   "summarize X" / "check X": start, middle and end of each scoped
               document plus its best matches, in reading order.
    - extract: "put every metric in a table": a wider hybrid net, in reading order.
    """
    scope = [d for d in (scope or []) if d in kb.docs]
    rows = kb.doc_rows(scope)
    if len(rows) == 0:
        return Selection([], "search", scope)

    dense_all = kb.dense_scores(kb.embedder.query(query))
    kw_all = kb.keyword_scores(query)
    dense, kw = dense_all[rows], kw_all[rows]
    pool = min(len(rows), max(k * 6, 30))
    dense_rank = [int(rows[i]) for i in np.argsort(-dense, kind="stable")[:pool]]
    kw_rank = [int(rows[i]) for i in np.argsort(-kw, kind="stable")[:pool] if kw[i] > 0]
    rankings = [dense_rank, kw_rank]
    if is_extraction(question) and _FIGURES_WANTED.search(question):
        # "every metric / figure / number": also rank passages by how many figures they hold.
        counts = np.array([len(_FIGURE.findall(kb.chunks[int(i)].text)) for i in rows])
        rankings.append([int(rows[i]) for i in np.argsort(-counts, kind="stable")[:pool] if counts[i] >= 3])
    fused = [i for i, _ in rrf(rankings)]
    top_score = float(dense.max())

    whole_docs = scope or (list(kb.docs) if len(kb.docs) <= 4 else [])
    if is_whole_document(question) and whole_docs and not is_extraction(question):
        mode = "whole"
        per_doc = max(MAX_CONTEXT // len(whole_docs), 3)
        picked: list[int] = []
        for doc_id in whole_docs:
            doc_rows = [kb.positions[c.id] for c in kb.docs[doc_id].chunks]
            best = [i for i in fused if kb.chunks[i].doc_id == doc_id][: max(1, per_doc // 3)]
            spread = [doc_rows[j] for j in _spread(len(doc_rows), max(per_doc - len(best), 2))]
            picked += list(dict.fromkeys(spread + best))[:per_doc]
        picked = picked[:MAX_CONTEXT]
        picked.sort(key=lambda i: (list(kb.docs).index(kb.chunks[i].doc_id), kb.chunks[i].ordinal))
    elif is_extraction(question):
        mode = "extract"
        picked = sorted(fused[: min(MAX_CONTEXT, max(k * 2, 8))])
    else:
        mode = "search"
        picked = fused[:k]

    return Selection(
        passages=[Passage(kb.chunks[i], float(dense_all[i])) for i in picked],
        mode=mode, scope=scope, top_score=top_score,
        complete=bool(scope) and len(set(picked)) == len(rows),
    )


# --- Confidence ---------------------------------------------------------------------
# Calibrated on bge-small-en-v1.5 with real papers: on-topic questions score
# 0.64-0.89, off-topic ones 0.38-0.45.
HIGH, MEDIUM = 0.68, 0.55


def score_level(score: float) -> str:
    return "High" if score >= HIGH else "Medium" if score >= MEDIUM else "Low"


def confidence(selection: Selection) -> dict:
    score = selection.top_score
    if selection.scope and (selection.mode == "whole" or selection.complete):
        level = "High"  # the user named the document and the model read across all of it
    elif score >= HIGH:
        level = "High"
    elif score >= MEDIUM:
        level = "Medium"
    else:
        level = "Low"
    return {"level": level, "score": round(score, 3)}
