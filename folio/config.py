"""Settings, read once from the environment (and .env, if present).

The API key is read here and only ever handed to the OpenAI client. It is never
logged, printed, returned by the API, or written to disk.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# LLM provider. Defaults point at Groq's free, OpenAI-compatible API.
# For a local Ollama model: LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.2 LLM_API_KEY=ollama
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
# Optional cheaper model for query rewriting. Empty = use the selected model.
LLM_FAST_MODEL = os.environ.get("LLM_FAST_MODEL", "")

# Local embeddings. bge-small reads up to 512 tokens, enough for 350-word chunks.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")

CACHE_DIR = Path(os.environ.get("FOLIO_CACHE_DIR", ROOT / ".cache" / "folio"))
AUDIT_LOG = Path(os.environ.get("FOLIO_AUDIT_LOG", ROOT / ".cache" / "audit.jsonl"))
CHAT_DB = Path(os.environ.get("FOLIO_DB", ROOT / ".cache" / "folio.db"))

# Shown in the sidebar's settings row.
USER_NAME = os.environ.get("FOLIO_USER_NAME", "You")
USER_INITIAL = os.environ.get("FOLIO_USER_INITIAL", "Y")[:2]

MAX_FILE_BYTES = 50 * 1024 * 1024


def has_server_key() -> bool:
    return bool(LLM_API_KEY)
