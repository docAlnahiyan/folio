"""
rag.py — Compatibility layer over the `folio` package.

The real pipeline lives in folio/ (loaders, chunking, hybrid index, answer
pipeline). This module keeps the original small API so streamlit_app.py and any
scripts written against it keep working:

    read_pdf(file, filename) -> list[Chunk]
    split_chunks(chunks) -> list[Chunk]
    KnowledgeBase().build(chunks); .retrieve(question, k) -> list[Chunk]
    answer_question(question, chunks, api_key, model) -> str
    list_models(api_key) -> list[str]
"""

from __future__ import annotations

from folio import llm as _llm
from folio.chunking import chunk_document
from folio.config import LLM_API_KEY as DEFAULT_API_KEY, LLM_BASE_URL, LLM_MODEL  # noqa: F401
from folio.index import KnowledgeBase as _KB
from folio.loaders import load_document
from folio.pipeline import ANSWER_SYSTEM, TrailerSplitter, _passages_block, parse_coverage
from folio.retrieval import Passage, select_passages
from folio.types import Chunk, Doc, doc_id_for

__all__ = ["Chunk", "KnowledgeBase", "read_pdf", "split_chunks", "answer_question", "list_models", "LLM_MODEL"]


def read_pdf(file, filename: str) -> list[Chunk]:
    """Read and chunk a PDF (sentence-aware, located chunks)."""
    data = file.read() if hasattr(file, "read") else open(file, "rb").read()
    _type, sections = load_document(filename, data)
    return chunk_document(doc_id_for(filename), filename, sections)


def split_chunks(chunks: list[Chunk], *_args, **_kwargs) -> list[Chunk]:
    """Chunking now happens in read_pdf; kept for backwards compatibility."""
    return list(chunks)


class KnowledgeBase:
    """Hybrid (dense + BM25) index over a fixed list of chunks."""

    def __init__(self):
        self._kb = _KB()

    def build(self, chunks: list[Chunk]) -> None:
        if not chunks:
            raise ValueError("No text found in the uploaded file(s).")
        self._kb.docs.clear()
        for c in chunks:
            self._kb.docs.setdefault(c.doc_id, Doc(id=c.doc_id, name=c.doc, type="")).chunks.append(c)
        vectors = self._kb.embedder.passages([c for d in self._kb.docs.values() for c in d.chunks])
        by_doc, start = {}, 0
        for doc_id, doc in self._kb.docs.items():
            by_doc[doc_id] = vectors[start:start + len(doc.chunks)]
            start += len(doc.chunks)
        self._kb._install(by_doc)

    def retrieve(self, question: str, k: int = 4) -> list[Chunk]:
        return [p.chunk for p in select_passages(self._kb, question, question, k=k).passages]


def answer_question(question: str, context_chunks: list[Chunk], api_key: str = "", model: str = "") -> str:
    """Grounded answer with [n] citations (non-streaming)."""
    passages = [Passage(c, 0.0) for c in context_chunks]
    response = _llm.client(api_key).chat.completions.create(
        model=model or LLM_MODEL,
        messages=[{"role": "system", "content": ANSWER_SYSTEM},
                  {"role": "user", "content": f"PASSAGES:\n\n{_passages_block(passages)}\n\nQUESTION: {question}"}],
        temperature=0.1,
    )
    splitter = TrailerSplitter()
    text = splitter.feed(_llm.strip_think(response.choices[0].message.content or "")) + splitter.finish()
    return parse_coverage(splitter.trailer, text)[1]


def list_models(api_key: str = "") -> list[str]:
    return [m["id"] for m in _llm.chat_models(_llm.list_model_ids(api_key))[0]]
