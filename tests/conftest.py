"""Shared fixtures: an offline embedder, a small knowledge base, and a scripted fake LLM."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Keep tests away from the real .cache (documents, chats, audit log) and any real key.
_TMP = Path(tempfile.mkdtemp(prefix="folio-tests-"))
os.environ["FOLIO_CACHE_DIR"] = str(_TMP / "folio")
os.environ["FOLIO_DB"] = str(_TMP / "folio.db")
os.environ["FOLIO_AUDIT_LOG"] = str(_TMP / "audit.jsonl")
os.environ["LLM_API_KEY"] = ""

from folio.index import KnowledgeBase, tokenize  # noqa: E402
from folio.types import Section  # noqa: E402


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder: no model download, stable scores."""

    name = "fake-embedder"
    dim = 256

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype="float32")
        for tok in tokenize(text):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def passages(self, chunks):
        return np.vstack([self._vec(f"{c.doc} {c.text}") for c in chunks]).astype("float32")

    def query(self, text):
        return self._vec(text)


def prose(topic: str, n: int) -> str:
    return " ".join(f"Sentence {i} about {topic} reports value {i}.{i}% for the {topic} study." for i in range(n))


@pytest.fixture
def kb() -> KnowledgeBase:
    base = KnowledgeBase(embedder=FakeEmbedder())
    base.add("OrbitalNet.pdf", "pdf", [
        Section("OrbitalNet detects early crop disease with 94.10% accuracy and overall performance 90.20%. "
                "It outperforms CNNs (+6.10%) and transformers (+15.30%).", "page", "2"),
        Section(prose("crop imagery", 60), "page", "3"),
        Section("The model surpasses LeafFormer by 3.70% in accuracy on the FieldScan test set.", "page", "30"),
        Section("Conclusion. OrbitalNet achieves 90.20% overall and 94.10% early-stage accuracy.", "page", "49"),
    ])
    base.add("cvJaneDoe.pdf", "pdf", [
        Section("Jane Doe. Summary: data engineer focused on analytics pipelines. Skills: Python, SQL, Airflow.", "page", "1"),
        Section("Projects: Route optimisation for delivery vans. Churn prediction for a subscription app.", "page", "2"),
    ])
    base.add("Finding Leaks When the Meters Lie.pdf", "pdf", [
        Section("We propose pressure-based localisation of water losses in municipal networks. " + prose("leak", 40), "page", "1"),
        Section(prose("meter readings", 40), "page", "2"),
    ])
    return base


# --- Fake OpenAI client --------------------------------------------------------------------


def _message(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _stream(text: str, size: int = 7):
    for i in range(0, len(text), size):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text[i:i + size]))])


class FakeLLM:
    """Scripted stand-in for openai.OpenAI. `script` maps a call kind to a reply or an exception."""

    def __init__(self, **script):
        self.script, self.calls = script, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _kind(self, kwargs) -> str:
        system = kwargs["messages"][0]["content"]
        if "You are Folio" in system:
            return "answer"
        if "verify answers" in system:
            return "check"
        if "extract structured" in system:
            return "extract"
        if "Rewrite" in system:
            return "rewrite"
        if "short title" in system:
            return "title"
        if "suggest questions" in system:
            return "suggest"
        return "other"

    def _create(self, **kwargs):
        kind = self._kind(kwargs)
        self.calls.append((kind, kwargs))
        reply = self.script.get(kind, "")
        if isinstance(reply, list):  # successive replies
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _stream(reply) if kwargs.get("stream") else _message(reply)


@pytest.fixture
def fake_llm():
    return FakeLLM
