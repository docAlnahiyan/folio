"""Core data types shared by loaders, the index and the answer pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field


@dataclass
class Section:
    """A located piece of a document before chunking: a page, sheet, slide or heading section."""
    text: str
    kind: str | None = None  # "page" | "sheet" | "slide" | "section" | "rows" | None
    loc: str | None = None   # "4", "Revenue", "Introduction", "2–51"
    tabular: bool = False    # lines are rows: chunk by line, not by sentence


@dataclass
class Chunk:
    """One retrievable passage, with a stable id and where it came from."""
    id: str        # "<doc_id>-<ordinal>", stable across restarts
    doc_id: str
    doc: str       # file name
    ordinal: int   # position within the document
    text: str
    kind: str | None = None
    loc: str | None = None

    @property
    def location(self) -> str:
        return f"{self.kind} {self.loc}" if self.kind and self.loc else ""

    @property
    def label(self) -> str:
        """Human source label, e.g. "report.pdf · page 4"."""
        return f"{self.doc} · {self.location}" if self.location else self.doc

    @property
    def source(self) -> str:  # older name for `label`, used by streamlit_app.py
        return self.label

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**{k: d.get(k) for k in ("id", "doc_id", "doc", "ordinal", "text", "kind", "loc")})


@dataclass
class Doc:
    id: str
    name: str
    type: str  # file extension without the dot
    chunks: list[Chunk] = field(default_factory=list)
    added_at: float = 0.0

    def summary(self) -> dict:
        return {"id": self.id, "name": self.name, "type": self.type, "chunks": len(self.chunks), "added_at": self.added_at}


def doc_id_for(name: str) -> str:
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
