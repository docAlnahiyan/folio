import csv
import io

import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from folio.chunking import MAX_WORDS, chunk_document, split_sentences
from folio.loaders import LoaderError, load_document
from folio.types import Section, doc_id_for


# --- Chunking -------------------------------------------------------------------------------

def test_split_sentences_respects_abbreviations_and_decimals():
    text = "Results improved, e.g. Accuracy rose to 90.20% overall. Dr. Smith et al. Agreed. Then it ended."
    assert split_sentences(text) == [
        "Results improved, e.g. Accuracy rose to 90.20% overall.",
        "Dr. Smith et al. Agreed.",
        "Then it ended.",
    ]


def test_chunks_are_sentence_aligned_sized_and_overlapping():
    sentences = [f"This is sentence number {i} with exactly ten words here." for i in range(120)]
    chunks = chunk_document("d1", "a.pdf", [Section(" ".join(sentences), "page", "4")])
    assert len(chunks) > 3
    for c in chunks[:-1]:
        words = len(c.text.split())
        assert 200 <= words <= MAX_WORDS, words
    for c in chunks:
        assert c.text.endswith("here."), "never cut mid-sentence"
        assert c.label == "a.pdf · page 4"
    # consecutive chunks share 1-2 whole sentences
    for a, b in zip(chunks, chunks[1:]):
        first_of_b = split_sentences(b.text)[0]
        assert first_of_b in split_sentences(a.text)[-2:]


def test_chunk_ids_are_stable_and_located():
    sections = [Section("Short page one.", "page", "1"), Section("Short page two.", "page", "2")]
    a = chunk_document(doc_id_for("x.pdf"), "x.pdf", sections)
    b = chunk_document(doc_id_for("x.pdf"), "x.pdf", sections)
    assert [c.id for c in a] == [c.id for c in b]
    assert [c.loc for c in a] == ["1", "2"], "chunks never span pages"


def test_small_tail_is_folded_into_previous_chunk():
    text = " ".join(f"Sentence {i} has a handful of words in it." for i in range(33)) + " Tiny end."
    chunks = chunk_document("d", "t.txt", [Section(text)])
    assert chunks[-1].text.endswith("Tiny end.")
    assert len(chunks[-1].text.split()) > 60


def test_tabular_chunks_repeat_the_header():
    lines = ["Columns: Month, Revenue"] + [f"Month: M{i}; Revenue: {i * 100} dollars recorded for the month" for i in range(80)]
    chunks = chunk_document("d", "f.xlsx", [Section("\n".join(lines), "sheet", "Revenue", tabular=True)])
    assert len(chunks) > 1
    assert all(c.text.startswith("Columns: Month, Revenue") for c in chunks)
    assert chunks[0].label == "f.xlsx · sheet Revenue"


# --- Loaders ----------------------------------------------------------------------------------

def _docx() -> bytes:
    d = Document()
    d.add_heading("Revenue", level=1)
    d.add_paragraph("Revenue was 4.2 million dollars")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Region", "Growth", "North", "21%"
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


def _xlsx() -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = "Revenue"
    ws.append(["Month", "Revenue"]); ws.append(["July", 1350000]); ws.append(["August", 1420000.0])
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _pptx() -> bytes:
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = "Pricing"; s.placeholders[1].text = "Pro costs 29 dollars"
    s.notes_slide.notes_text_frame.text = "Mention the discount."
    buf = io.BytesIO(); p.save(buf); return buf.getvalue()


def _csv() -> bytes:
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["id", "amount"]); w.writerow([1, 50]); w.writerow([2, 75])
    return buf.getvalue().encode()


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter(); w.add_blank_page(612, 792)
    buf = io.BytesIO(); w.write(buf); return buf.getvalue()


@pytest.mark.parametrize("name,data,kind,label,contains", [
    ("r.docx", _docx(), "docx", "section Revenue", "North | 21%"),
    ("f.xlsx", _xlsx(), "xlsx", "sheet Revenue", "Month: August; Revenue: 1420000"),
    ("d.pptx", _pptx(), "pptx", "slide 1", "Speaker notes: Mention the discount."),
    ("o.csv", _csv(), "csv", "rows 2–3", "id: 2; amount: 75"),
    ("n.md", b"# Plan\n\nShip on 12 December.\n", "md", "section Plan", "Ship on 12 December."),
    ("t.txt", "Café stock is 12,400 units.".encode(), "txt", None, "Café stock"),
])
def test_loaders_produce_located_sections(name, data, kind, label, contains):
    file_type, sections = load_document(name, data)
    assert file_type == kind
    chunks = chunk_document("d", name, sections)
    assert any(contains in c.text for c in chunks)
    if label:
        assert any(c.location == label for c in chunks)


def test_scanned_pdf_is_a_clear_error():
    with pytest.raises(LoaderError) as e:
        load_document("scan.pdf", _blank_pdf())
    assert e.value.code == "scanned"
    assert "OCR isn't enabled" in e.value.message


@pytest.mark.parametrize("name,code", [("a.key", "unsupported"), ("b.doc", "unsupported"), ("c.pdf", "unreadable"),
                                       ("e.txt", "empty")])
def test_bad_files_get_friendly_errors(name, code):
    data = b"%PDF-1.4 nope" if name.endswith(".pdf") else (b"   " if name.endswith(".txt") else b"x")
    with pytest.raises(LoaderError) as e:
        load_document(name, data)
    assert e.value.code == code
    assert e.value.message
