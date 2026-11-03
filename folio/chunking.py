"""Sentence-aware chunking.

Chunks are built from whole sentences (or whole rows, for tables), aiming for
200-350 words, and consecutive chunks share the last 1-2 sentences so a fact
that straddles a boundary is still found intact. Chunks never span two pages,
sheets or slides, so every chunk has one exact location.
"""

from __future__ import annotations

import re

from .types import Chunk, Section

MIN_WORDS = 200
TARGET_WORDS = 280
MAX_WORDS = 350

_ABBREVIATIONS = {
    "e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "no.", "fig.", "figs.",
    "eq.", "approx.", "inc.", "ltd.", "co.", "jr.", "sr.", "u.s.", "et al.", "al.", "sec.", "vol.", "pp.",
}
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9•\-–])")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking after common abbreviations or decimals."""
    parts = [p.strip() for p in _SENTENCE_BREAK.split(text) if p.strip()]
    sentences: list[str] = []
    for part in parts:
        if sentences:
            tail = sentences[-1].lower()
            last_word = tail.rsplit(" ", 1)[-1]
            if last_word in _ABBREVIATIONS or tail.endswith(" et al.") or re.search(r"\b[a-z]\.$", tail):
                sentences[-1] += " " + part
                continue
        sentences.append(part)
    return sentences


def _words(s: str) -> int:
    return len(s.split())


def _units(section: Section) -> list[str]:
    raw = [line.strip() for line in section.text.split("\n") if line.strip()] if section.tabular else split_sentences(section.text)
    units: list[str] = []
    for u in raw:
        words = u.split()
        if len(words) <= MAX_WORDS:
            units.append(u)
        else:  # a single "sentence" longer than a chunk (e.g. a run-on table dump)
            units.extend(" ".join(words[i:i + TARGET_WORDS]) for i in range(0, len(words), TARGET_WORDS))
    return units


def _overlap(units: list[str]) -> list[str]:
    """The last 1-2 sentences of a chunk, carried into the next one."""
    if not units:
        return []
    last = units[-1]
    if len(units) >= 2 and _words(units[-2]) + _words(last) <= 60:
        return units[-2:]
    return [last] if _words(last) <= 90 else []


def chunk_section(section: Section) -> list[list[str]]:
    """Group a section's sentences into chunks; returns each chunk as its list of sentences."""
    units = _units(section)
    # Tables repeat their "Columns: ..." line in every chunk so values stay labelled.
    header = units[0] if section.tabular and units and units[0].startswith("Columns:") else None
    groups: list[list[str]] = []
    current: list[str] = []
    count = 0
    fresh = 0  # sentences in `current` that no earlier chunk contains

    for unit in units:
        n = _words(unit)
        if fresh and (count + n > MAX_WORDS or count >= TARGET_WORDS):
            groups.append(current)
            current = ([header] if header else []) if section.tabular else _overlap(current)
            count = sum(_words(u) for u in current)
            fresh = 0
        current.append(unit)
        count += n
        fresh += 1

    if fresh:
        tail_new = current[len(current) - fresh:]
        tail_words = sum(_words(u) for u in tail_new)
        # Fold a small remainder into the previous chunk rather than leaving a scrap.
        if groups and tail_words < 60 and sum(_words(u) for u in groups[-1]) + tail_words <= MAX_WORDS + 60:
            groups[-1] = groups[-1] + tail_new
        else:
            groups.append(current)
    return groups


def chunk_document(doc_id: str, doc_name: str, sections: list[Section]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        joiner = "\n" if section.tabular else " "
        for group in chunk_section(section):
            ordinal = len(chunks)
            chunks.append(Chunk(
                id=f"{doc_id}-{ordinal:04d}", doc_id=doc_id, doc=doc_name, ordinal=ordinal,
                text=joiner.join(group), kind=section.kind, loc=section.loc,
            ))
    return chunks
