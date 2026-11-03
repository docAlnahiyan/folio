"""Turn uploaded files into located Sections: PDF pages, Word sections, sheets, slides."""

from __future__ import annotations

import csv
import io
import re
from pathlib import PurePath

from .types import Section

SUPPORTED = {
    ".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "md", ".markdown": "md",
    ".csv": "csv", ".xlsx": "xlsx", ".pptx": "pptx",
}
_LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}

SUPPORTED_HINT = "PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), CSV, Markdown or plain text"


class LoaderError(Exception):
    """A file we can't index, with a message that is safe to show the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def file_type(name: str) -> str | None:
    return SUPPORTED.get(PurePath(name).suffix.lower())


def clean_text(text: str) -> str:
    text = text.replace(" ", " ").replace("\x00", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # re-join words hyphenated across lines
    return re.sub(r"\s+", " ", text).strip()


def load_document(name: str, data: bytes) -> tuple[str, list[Section]]:
    """Return (file type, sections). Raises LoaderError for anything we can't index."""
    suffix = PurePath(name).suffix.lower()
    kind = SUPPORTED.get(suffix)
    if kind is None:
        if suffix in _LEGACY:
            raise LoaderError("unsupported", f"Old {suffix} files aren't supported. Save it as {_LEGACY[suffix]} and try again.")
        shown = suffix or "this type of"
        raise LoaderError("unsupported", f"Folio can't read {shown} files. Try {SUPPORTED_HINT}.")
    try:
        sections = _LOADERS[kind](data)
    except LoaderError:
        raise
    except Exception:
        raise LoaderError("unreadable", "Couldn't read this file. It may be damaged or in an unexpected format.") from None
    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise LoaderError("empty", "No text found in this file.")
    return kind, sections


# --- PDF ------------------------------------------------------------------
def _load_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            ok = reader.decrypt("")
        except Exception:
            ok = 0
        if not ok:
            raise LoaderError("encrypted", "This PDF is password-protected. Remove the password and try again.")
    sections = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = clean_text(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            sections.append(Section(text=text, kind="page", loc=str(i)))
    if reader.pages and not sections:
        raise LoaderError("scanned", "Scanned pages, no text found. OCR isn't enabled.")
    return sections


# --- Word -----------------------------------------------------------------
def _load_docx(data: bytes) -> list[Section]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(io.BytesIO(data))
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []

    def flush():
        text = " ".join(buf).strip()
        if text:
            sections.append(Section(text=clean_text(text), kind="section" if heading else None, loc=heading))
        buf.clear()

    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(block, document)
            text = para.text.strip()
            style = (para.style.name if para.style is not None else "") or ""
            if text and (style.startswith("Heading") or style == "Title"):
                flush()
                heading = text[:80]
                buf.append(text + ".")  # keep the heading in the text so it's searchable
            elif text:
                buf.append(text if text[-1] in ".!?:;" else text + ".")
        elif tag == "tbl":
            for row in Table(block, document).rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    buf.append(" | ".join(cells) + ".")
    flush()
    return sections


# --- Plain text and Markdown ------------------------------------------------
def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _load_txt(data: bytes) -> list[Section]:
    return [Section(text=clean_text(_decode(data)))]


def _load_md(data: bytes) -> list[Section]:
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []
    for line in _decode(data).splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            if "".join(buf).strip():
                sections.append(Section(text=clean_text("\n".join(buf)), kind="section" if heading else None, loc=heading))
            heading, buf = m.group(1)[:80], [m.group(1) + "."]
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append(Section(text=clean_text("\n".join(buf)), kind="section" if heading else None, loc=heading))
    return sections


# --- Tables: CSV and Excel --------------------------------------------------
def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows_to_lines(rows: list[list[str]]) -> tuple[str | None, list[tuple[int, str]]]:
    """Render rows as "Header: value; Header: value" lines, keyed by 1-based row number."""
    rows = [(i, r) for i, r in enumerate(rows, start=1) if any(c for c in r)]
    if not rows:
        return None, []
    header_row, header = rows[0]
    has_header = len(rows) > 1 and all(c and not re.fullmatch(r"[-+.,%\d\s$€£]+", c) for c in header if c)
    lines = []
    for num, row in rows[1:] if has_header else rows:
        if has_header:
            pairs = [f"{(header[j] if j < len(header) and header[j] else f'Column {j + 1}')}: {v}"
                     for j, v in enumerate(row) if v]
        else:
            pairs = [v for v in row if v]
        lines.append((num, "; ".join(pairs)))
    return ("Columns: " + ", ".join(h for h in header if h)) if has_header else None, lines


def _load_csv(data: bytes) -> list[Section]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
    columns, lines = _rows_to_lines(rows)
    sections = []
    for start in range(0, len(lines), 40):  # 40 rows per located block
        block = lines[start:start + 40]
        body = "\n".join(([columns] if columns else []) + [line for _, line in block])
        sections.append(Section(text=body, kind="rows", loc=f"{block[0][0]}–{block[-1][0]}", tabular=True))
    return sections


def _load_xlsx(data: bytes) -> list[Section]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sections = []
    try:
        for ws in wb.worksheets:
            rows = [[_fmt(v) for v in row] for row in ws.iter_rows(values_only=True)]
            columns, lines = _rows_to_lines(rows)
            if lines:
                body = "\n".join(([columns] if columns else []) + [line for _, line in lines])
                sections.append(Section(text=body, kind="sheet", loc=ws.title, tabular=True))
    finally:
        wb.close()
    return sections


# --- PowerPoint -------------------------------------------------------------
def _load_pptx(data: bytes) -> list[Section]:
    from pptx import Presentation

    deck = Presentation(io.BytesIO(data))
    sections = []
    for i, slide in enumerate(deck.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        parts.append(t if t[-1] in ".!?:;" else t + ".")
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells) + ".")
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append("Speaker notes: " + notes)
        text = clean_text(" ".join(parts))
        if text:
            sections.append(Section(text=text, kind="slide", loc=str(i)))
    return sections


_LOADERS = {
    "pdf": _load_pdf, "docx": _load_docx, "txt": _load_txt, "md": _load_md,
    "csv": _load_csv, "xlsx": _load_xlsx, "pptx": _load_pptx,
}
