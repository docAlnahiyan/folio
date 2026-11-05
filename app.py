"""Entry point for the Hugging Face Space (Gradio SDK, which runs `python app.py`).

It starts the same FastAPI app as `uvicorn server:app`, in demo mode, on the port
Spaces expects. Locally, keep using `uvicorn server:app --reload`.
"""

import os

os.environ.setdefault("FOLIO_DEMO", "1")
for name, path in (("FOLIO_CACHE_DIR", "/tmp/folio/cache"), ("FOLIO_DB", "/tmp/folio/folio.db"),
                   ("FOLIO_AUDIT_LOG", "/tmp/folio/audit.jsonl")):
    os.environ.setdefault(name, path)

if os.environ.get("SPACES_ZERO_GPU"):
    # ZeroGPU hardware expects at least one GPU function; Folio runs on CPU, so this is never called.
    # If the helper library can't load, carry on: the app itself doesn't need it.
    try:
        import spaces

        @spaces.GPU
        def _unused() -> None:
            pass
    except Exception as exc:  # noqa: BLE001
        print(f"ZeroGPU helper unavailable, running on CPU only: {exc}", flush=True)

import uvicorn  # noqa: E402

from server import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_SERVER_PORT") or os.environ.get("PORT") or 7860)
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
