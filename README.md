# Folio

**Ask your documents anything. Cited, fact-checked answers from PDF, Word,
Excel and PowerPoint files.**

Folio is a document assistant for teams that need answers they can trust:
analysts, researchers, legal and operations staff, anyone who has to point to
the exact page a fact came from. Every factual sentence is cited, the
supporting sentence is highlighted in the source, and each answer is checked
against the passages before you rely on it. When the documents don't cover
part of a question, Folio says so instead of guessing.

Documents are parsed and embedded locally; only the question and the
retrieved passages are sent to the language model (Groq's free API by
default, or a local Ollama model for fully offline use).

## Key features

- **Cited answers.** Every factual sentence carries a numbered citation.
  Click it to open the source passage with the supporting sentence
  highlighted.
- **Claim check.** After answering, Folio checks each claim against its
  sources. "4 of 4 claims supported" only appears when the check ran and
  every claim passed; otherwise the unsupported claims are listed.
- **Conflict detection.** When passages report different figures for what
  looks like the same thing, they are shown side by side, with whether it's a
  real contradiction or, for example, two different baselines.
- **Gap report.** Questions the documents only partly cover get an "In the
  documents / Not in the documents" breakdown.
- **Cited tables.** "Put every metric in a table" returns a table where each
  row links to its page and exact quote. Values not found word for word in the
  source are flagged. Copy it as CSV.
- **Saved chats.** Conversations are stored in SQLite with everything needed
  to re-render each answer. Search, reopen and delete them from the sidebar;
  follow-up questions use the chat's earlier turns.
- **Multi-format files.** PDF, Word (.docx), Excel (.xlsx), PowerPoint
  (.pptx), CSV, Markdown and plain text, with precise source labels such as
  `report.pdf · page 4` or `finance.xlsx · sheet Revenue`. Scanned PDFs and
  unsupported files get a clear message.

Also: streaming answers with a Stop button, document scoping ("summarize the
CV" reads just the CV), High / Medium / Low confidence, Fast mode for free
rate limits, an audit log of every answer, and light and dark themes on
desktop, tablet and phone.

## Screenshots

_Screenshots coming soon._

## Setup

You need Python 3.10 or newer (tested on 3.13, macOS arm64).

```bash
git clone https://github.com/docAlnahiyan/folio.git
cd folio
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and set `LLM_API_KEY` to a free Groq key from
<https://console.groq.com> (no credit card needed).

- With `LLM_API_KEY` set, Folio uses it for every request and hides the key
  field. **Anyone who can open your Folio then uses your Groq quota**, so
  don't expose it publicly with a key set.
- Leave it empty and each visitor pastes their own key in the Settings
  popover instead. It stays in that browser tab and is never stored or
  logged.
- To run fully offline with Ollama, see the comments in `.env.example`.

## Run

```bash
uvicorn server:app --reload
```

Then open <http://localhost:8000>. On first start Folio downloads a small
embedding model (`BAAI/bge-small-en-v1.5`, about 130 MB) once.

## Tests

```bash
python -m pytest -q
```

The suite runs offline in about a second, using a fake embedder and a mocked
language model. It covers chunking, every file loader and its error cases,
retrieval and document scoping, rank fusion, the JSON parsing and fallbacks
of the answer pipeline, the chat database and the chat API.

## Architecture

```
Browser (HTML/CSS/JS, no build step)
   │  REST + Server-Sent Events
FastAPI (server.py)
   ├── Loaders ─ PDF, DOCX, XLSX, PPTX, CSV, MD, TXT → located sections
   ├── Chunking ─ sentence-aware, 200–350 words, never across pages
   ├── Retrieval ─ BM25 (rank-bm25) + dense (sentence-transformers + FAISS),
   │               merged with reciprocal rank fusion; document scoping
   ├── Answer pipeline ─ follow-up rewrite → stream answer with citations
   │                     → claim check + conflict detection (JSON mode)
   └── Storage ─ FAISS index + chunks on disk, chats in SQLite, audit log
```

- **FastAPI** serves the UI and a small JSON API; answers stream as
  Server-Sent Events, so text appears as it is written and Stop cancels the
  model call.
- **Hybrid retrieval** combines keyword search (BM25), which finds exact
  codes and figures, with dense embeddings (FAISS), which find paraphrases.
  Reciprocal rank fusion merges the two rankings. Questions that name a file
  are scoped to it, and "summarize X" reads the start, middle and end of the
  document rather than just the closest passages.
- **SQLite** (standard library) stores saved chats and the full payload of
  every answer, so reopened chats render exactly as they first appeared.
- Every extra step (claim check, conflicts, titles, suggestions) fails
  gracefully: if it can't run, the answer still stands and the UI simply
  omits that extra.

### Project layout

```
server.py            FastAPI app: API + serves the web UI
folio/
  config.py          settings (.env), paths
  loaders.py         file types → located sections
  chunking.py        sentence-aware chunking
  index.py           FAISS + BM25 knowledge base, persistence
  retrieval.py       rank fusion, document scoping, confidence
  llm.py             client, model catalogue, JSON mode with fallbacks
  pipeline.py        rewrite → retrieve → stream → check → audit
  chats.py           saved chats (SQLite) and titles
static/              index.html, style.css, app.js, vendor/rough.js (MIT)
tests/               offline pytest suite
rag.py               small compatibility layer for streamlit_app.py
streamlit_app.py     original Streamlit prototype (optional)
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/state` | Indexed documents; whether a server key is set |
| `POST` | `/api/upload` | Multipart `files`; one result per file |
| `DELETE` | `/api/docs/{id}` | Remove a document |
| `GET` | `/api/models` | Available chat models with friendly names |
| `GET` | `/api/suggestions` | Three example questions for the current documents |
| `GET` | `/api/chats?q=` | Saved chats, newest first; `q` searches them |
| `GET` / `PATCH` / `DELETE` | `/api/chats/{id}` | Read, rename or delete a chat |
| `POST` | `/api/chat` | `{question, chat_id?, model, k, fast, doc}`; streams SSE events |

When no server key is set, send the visitor's key in the `x-api-key` header.
