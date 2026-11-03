"""The knowledge base: chunk store, dense (FAISS) + keyword (BM25) indexes, persistence."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from .chunking import chunk_document, split_sentences
from .config import EMBED_MODEL
from .types import Chunk, Doc, Section, doc_id_for

STORE_VERSION = 2

_STOPWORDS = frozenset(
    "a an and are as at be by for from has have he her his i in is it its of on or our she that the "
    "their them they this to was we were what when where which who why will with you your do does did "
    "can could should would about into than then there these those how not no yes any all me my so if "
    "tell give show list find please".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens for BM25, with stopwords dropped and simple plural folding."""
    out = []
    for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower()):
        if t in _STOPWORDS or (len(t) < 2 and not t.isdigit()):
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss") and not t[0].isdigit():
            t = t[:-1]
        out.append(t)
    return out


class Embedder:
    """sentence-transformers wrapper that knows each model's query/passage conventions."""

    def __init__(self, model_name: str = EMBED_MODEL):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self.model = SentenceTransformer(model_name)
        self.model.max_seq_length = min(512, self.model.max_seq_length or 512)
        self.query_prefix = "Represent this sentence for searching relevant passages: " if "bge" in model_name.lower() else ""

    def _encode(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True,
                                 show_progress_bar=False, batch_size=32).astype("float32")

    def passages(self, chunks: list[Chunk]) -> np.ndarray:
        # The file name and location travel with the text, so "the CV" can match a CV's passages.
        return self._encode([f"{c.doc} ({c.location})\n{c.text}" if c.location else f"{c.doc}\n{c.text}" for c in chunks])

    def query(self, text: str) -> np.ndarray:
        return self._encode([self.query_prefix + text])[0]


class KnowledgeBase:
    def __init__(self, embedder: Embedder | None = None):
        self.embedder = embedder or Embedder()
        self.docs: dict[str, Doc] = {}  # doc_id -> Doc, in upload order
        self.chunks: list[Chunk] = []
        self.vectors = np.zeros((0, 0), dtype="float32")
        self.index: faiss.Index | None = None
        self.bm25: BM25Okapi | None = None
        self.positions: dict[str, int] = {}  # chunk id -> row in self.chunks / self.vectors

    # --- building ----------------------------------------------------------
    def _install(self, vectors_by_doc: dict[str, np.ndarray]) -> None:
        self.chunks = [c for d in self.docs.values() for c in d.chunks]
        self.positions = {c.id: i for i, c in enumerate(self.chunks)}
        if not self.chunks:
            self.vectors, self.index, self.bm25 = np.zeros((0, 0), dtype="float32"), None, None
            return
        self.vectors = np.vstack([vectors_by_doc[d] for d in self.docs])
        self.index = faiss.IndexFlatIP(self.vectors.shape[1])
        self.index.add(self.vectors)
        self.bm25 = BM25Okapi([tokenize(c.text) or ["_"] for c in self.chunks])

    def _vectors_by_doc(self) -> dict[str, np.ndarray]:
        out = {}
        for doc_id, doc in self.docs.items():
            rows = [self.positions[c.id] for c in doc.chunks]
            out[doc_id] = self.vectors[rows]
        return out

    def add(self, name: str, file_type: str, sections: list[Section]) -> Doc:
        """Chunk, embed and index a document. Re-adding a file with the same name replaces it."""
        doc_id = doc_id_for(name)
        chunks = chunk_document(doc_id, name, sections)
        if not chunks:
            raise ValueError("No text found in this file.")
        new_vectors = self.embedder.passages(chunks)
        existing = {d: v for d, v in self._vectors_by_doc().items() if d != doc_id}
        self.docs.pop(doc_id, None)
        self.docs[doc_id] = Doc(id=doc_id, name=name, type=file_type, chunks=chunks, added_at=time.time())
        self._install({**existing, doc_id: new_vectors})
        return self.docs[doc_id]

    def remove(self, doc_id: str) -> bool:
        if doc_id not in self.docs:
            return False
        keep = {d: v for d, v in self._vectors_by_doc().items() if d != doc_id}
        del self.docs[doc_id]
        self._install(keep)
        return True

    def clear(self) -> None:
        self.docs.clear()
        self._install({})

    # --- lookups -------------------------------------------------------------
    def doc_rows(self, doc_ids: list[str] | None) -> np.ndarray:
        if not doc_ids:
            return np.arange(len(self.chunks))
        wanted = set(doc_ids)
        return np.array([i for i, c in enumerate(self.chunks) if c.doc_id in wanted], dtype=int)

    def dense_scores(self, query_vec: np.ndarray) -> np.ndarray:
        """Cosine similarity of every chunk to the query."""
        if self.index is None:
            return np.zeros(0, dtype="float32")
        scores, idx = self.index.search(query_vec[None, :], len(self.chunks))
        out = np.empty(len(self.chunks), dtype="float32")
        out[idx[0]] = scores[0]
        return out

    def keyword_scores(self, query: str) -> np.ndarray:
        if self.bm25 is None:
            return np.zeros(0, dtype="float32")
        terms = tokenize(query)
        if not terms:
            return np.zeros(len(self.chunks), dtype="float32")
        return np.asarray(self.bm25.get_scores(terms), dtype="float32")

    # --- persistence -----------------------------------------------------------
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STORE_VERSION,
            "embed_model": self.embedder.name,
            "docs": [{**d.summary(), "chunks": [c.to_dict() for c in d.chunks]} for d in self.docs.values()],
        }
        _atomic_write(directory / "docs.json", json.dumps(payload).encode("utf-8"))
        if self.index is not None:
            tmp = directory / "index.faiss.tmp"
            faiss.write_index(self.index, str(tmp))
            os.replace(tmp, directory / "index.faiss")
        else:
            (directory / "index.faiss").unlink(missing_ok=True)
        (directory / "index.json").unlink(missing_ok=True)  # v1 leftover

    def load(self, directory: Path) -> bool:
        """Restore saved documents. Migrates the v1 format. Returns True if anything changed on disk."""
        try:
            payload = json.loads((directory / "docs.json").read_text("utf-8"))
        except FileNotFoundError:
            return False
        if payload.get("version") != STORE_VERSION:
            backup = directory / "docs.v1.json"
            if not backup.exists():
                backup.write_bytes((directory / "docs.json").read_bytes())
            self._migrate_v1(payload)
            return True
        for d in payload.get("docs", []):
            chunks = [Chunk.from_dict(c) for c in d["chunks"]]
            self.docs[d["id"]] = Doc(id=d["id"], name=d["name"], type=d.get("type", ""), chunks=chunks,
                                     added_at=d.get("added_at", 0.0))
        all_chunks = [c for d in self.docs.values() for c in d.chunks]
        vectors = None
        if payload.get("embed_model") == self.embedder.name:
            try:
                index = faiss.read_index(str(directory / "index.faiss"))
                if index.ntotal == len(all_chunks):
                    vectors = index.reconstruct_n(0, index.ntotal)
            except Exception:
                vectors = None
        reembedded = vectors is None
        if reembedded:  # missing, stale or different embedding model: re-embed
            vectors = self.embedder.passages(all_chunks) if all_chunks else np.zeros((0, 0), dtype="float32")
        by_doc, start = {}, 0
        for doc_id, doc in self.docs.items():
            by_doc[doc_id] = vectors[start:start + len(doc.chunks)]
            start += len(doc.chunks)
        self._install(by_doc)
        return reembedded and bool(all_chunks)

    def _migrate_v1(self, payload: dict) -> None:
        """v1 stored overlapping word-window chunks with "name (page n)" sources.
        Rebuild each page's text by stitching chunks back together, then re-chunk."""
        for d in payload.get("docs", []):
            pages: dict[str, list[str]] = {}
            for c in d.get("chunks", []):
                m = re.match(r"^(.*) \(page (\d+)\)$", c.get("source", ""))
                page = m.group(2) if m else ""
                pages.setdefault(page, []).append(c["text"])
            sections = [Section(text=_stitch(texts), kind="page" if p else None, loc=p or None)
                        for p, texts in pages.items()]
            name = d["name"]
            suffix = Path(name).suffix.lower().lstrip(".") or "pdf"
            self.add(name, suffix, sections)


def _stitch(texts: list[str]) -> str:
    """Join consecutive overlapping chunks, dropping the repeated words at each seam."""
    words = texts[0].split()
    for t in texts[1:]:
        nxt = t.split()
        best = 0
        for n in range(min(len(words), len(nxt), 120), 0, -1):
            if words[-n:] == nxt[:n]:
                best = n
                break
        words += nxt[best:]
    return " ".join(words)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


__all__ = ["KnowledgeBase", "Embedder", "tokenize", "split_sentences"]
