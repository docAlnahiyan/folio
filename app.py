"""Entry point for the Hugging Face Space (Gradio SDK, which runs `python app.py`).

It starts the same FastAPI app as `uvicorn server:app`, in demo mode, on the port
Spaces expects. Locally, keep using `uvicorn server:app --reload`.
"""

import os

os.environ.setdefault("FOLIO_DEMO", "1")
for name, path in (("FOLIO_CACHE_DIR", "/tmp/folio/cache"), ("FOLIO_DB", "/tmp/folio/folio.db"),
                   ("FOLIO_AUDIT_LOG", "/tmp/folio/audit.jsonl")):
    os.environ.setdefault(name, path)

# Folio's embedding model runs on the CPU. On ZeroGPU, PyTorch reports a GPU in the main
# process, which sentence-transformers would otherwise try to use.
os.environ.setdefault("FOLIO_EMBED_DEVICE", "cpu")

if os.environ.get("SPACES_ZERO_GPU", "").lower() in ("1", "t", "true"):
    # ZeroGPU hardware (the free tier here) requires at least one @spaces.GPU function,
    # reported at startup. The `spaces` helper normally reports it when gr.Blocks.launch()
    # runs; Folio serves its own FastAPI app, so it registers a placeholder (never
    # called: Folio needs no GPU) and makes the same startup report itself.
    # `spaces` must be imported before torch.
    import spaces

    @spaces.GPU
    def _gpu_placeholder() -> None:
        return None

    spaces.zero.startup()

import uvicorn  # noqa: E402

from server import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_SERVER_PORT") or os.environ.get("PORT") or 7860)
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
