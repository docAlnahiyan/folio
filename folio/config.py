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


# --- Public demo mode (FOLIO_DEMO=1), e.g. on Hugging Face Spaces -------------------------
# Off by default: local use is unchanged. See README "Live demo".
DEMO = os.environ.get("FOLIO_DEMO", "") == "1"
DEMO_DIR = Path(os.environ.get("FOLIO_DEMO_DIR", ROOT / "demo"))
DEMO_PER_HOUR = int(os.environ.get("FOLIO_DEMO_PER_HOUR", "8"))  # questions per visitor per hour
DEMO_DAILY_CAP = int(os.environ.get("FOLIO_DEMO_DAILY_CAP", "300"))  # questions per day, all visitors
DEMO_MAX_UPLOADS = int(os.environ.get("FOLIO_DEMO_MAX_UPLOADS", "3"))  # files per visitor
DEMO_MAX_UPLOAD_BYTES = int(os.environ.get("FOLIO_DEMO_MAX_UPLOAD_MB", "5")) * 1024 * 1024
DEMO_SESSION_TTL = int(os.environ.get("FOLIO_DEMO_SESSION_TTL", "3600"))  # seconds of inactivity
DEMO_MAX_QUESTION = 1000  # characters
AUDIT_RETENTION_DAYS = 7  # demo only: older audit entries are pruned
# Secure cookies need HTTPS (Spaces, or localhost in browsers). Tests over plain HTTP turn it off.
COOKIE_SECURE = os.environ.get("FOLIO_COOKIE_SECURE", "1") != "0"
PORTFOLIO_URL = os.environ.get("FOLIO_PORTFOLIO_URL", "https://docalnahiyan.github.io")
REPO_URL = os.environ.get("FOLIO_REPO_URL", "https://github.com/docAlnahiyan/folio")
DEMO_AUTHOR = os.environ.get("FOLIO_DEMO_AUTHOR", "Al-Nahiyan Khushbu")
