# Folio as a public demo on Hugging Face Spaces (Docker SDK).
# Spaces runs the container as uid 1000 and routes traffic to app_port 7860.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 user
WORKDIR /home/user/app

# CPU-only PyTorch (the default wheel bundles ~2 GB of CUDA libraries a Space can't use).
COPY requirements-space.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0" \
 && pip install -r requirements-space.txt

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    FOLIO_DEMO=1 \
    FOLIO_CACHE_DIR=/tmp/folio/cache \
    FOLIO_DB=/tmp/folio/folio.db \
    FOLIO_AUDIT_LOG=/tmp/folio/audit.jsonl

# Download the embedding model at build time, so the first visitor doesn't wait for it.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY --chown=user . .

EXPOSE 7860
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
