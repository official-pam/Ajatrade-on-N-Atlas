"""Host the N-ATLAS server on Modal (https://modal.com).

One-time setup, from the AjaTrade folder on your computer:

    pip install modal
    modal setup                      # opens the browser to log in
    modal secret create natlas-secrets HF_TOKEN=hf_... NATLAS_SERVER_KEY=<long-random-secret>
    modal run natlas_server/modal_app.py::download     # copies the 5 models into a Modal volume (~17 GB, CPU only)
    modal deploy natlas_server/modal_app.py

`modal deploy` prints the URL, e.g. https://<workspace>--ajatrade-natlas-serve.modal.run
Put it in the bot's NATLAS_BASE_URL and the secret in NATLAS_API_KEY.

Cost and cold starts: the GPU only runs (and bills) while requests come in,
then shuts down after SCALEDOWN_SECONDS of silence. A cold start reloads the
model from the volume (about 1 to 3 minutes), so while real farmers are using
the bot, keep one container warm:

    NATLAS_MIN_CONTAINERS=1 modal deploy natlas_server/modal_app.py

and set it back to 0 afterwards. One L4 left on costs about $0.80 an hour.
"""

from __future__ import annotations

import os
from pathlib import Path

import modal

APP_NAME = "ajatrade-natlas"
GPU = os.getenv("NATLAS_GPU", "L4")  # 24 GB: bf16 8B model + four ASR models
MIN_CONTAINERS = int(os.getenv("NATLAS_MIN_CONTAINERS", "0"))
SCALEDOWN_SECONDS = int(os.getenv("NATLAS_SCALEDOWN_SECONDS", "900"))
CACHE_DIR = "/cache"
HF_HOME = f"{CACHE_DIR}/hf"

MODELS = (
    "NCAIR1/N-ATLaS",
    "NCAIR1/Yoruba-ASR",
    "NCAIR1/Hausa-ASR",
    "NCAIR1/Igbo-ASR",
    "NCAIR1/NigerianAccentedEnglish",
    "openai/whisper-small",  # processor fallback used by server.py
)

HERE = Path(__file__).parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "torch==2.5.1",
        "transformers==4.51.3",
        "accelerate>=1.6",
        "numpy<2.3",
        "sentencepiece",
        "huggingface_hub[hf_transfer]>=0.30",
        "fastapi==0.115.12",
        "python-multipart==0.0.20",
        "pydantic==2.11.4",
    )
    .env({"HF_HOME": HF_HOME, "HF_HUB_ENABLE_HF_TRANSFER": "1"})
    .add_local_file(HERE / "server.py", "/root/server.py")
)

cache = modal.Volume.from_name("natlas-hf-cache", create_if_missing=True)
secrets = [modal.Secret.from_name("natlas-secrets")]

app = modal.App(APP_NAME, image=image)


@app.function(volumes={CACHE_DIR: cache}, secrets=secrets, timeout=3600)
def download() -> None:
    """Copy the gated models into the volume once (no GPU needed)."""
    from huggingface_hub import HfApi, snapshot_download

    token = os.environ.get("HF_TOKEN")
    api = HfApi(token=token)
    for repo in MODELS:
        files = api.list_repo_files(repo)
        # Prefer safetensors; fall back to .bin weights only when a repo has none.
        weights = ["*.safetensors"] if any(f.endswith(".safetensors") for f in files) else ["*.bin"]
        print(f"Downloading {repo} ({weights[0]}) ...")
        snapshot_download(
            repo,
            token=token,
            allow_patterns=["*.json", "*.txt", "*.model", "*.tiktoken", "*.jinja", *weights],
        )
    cache.commit()
    print("All models are in the volume.")


@app.function(
    gpu=GPU,
    volumes={CACHE_DIR: cache},
    secrets=secrets,
    timeout=600,  # longest single request
    startup_timeout=1200,  # loading the models from the volume
    scaledown_window=SCALEDOWN_SECONDS,
    min_containers=MIN_CONTAINERS,
    max_containers=1,  # one GPU; requests queue inside the server
)
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def serve():
    import server  # natlas_server/server.py

    # Load here so Modal's startup_timeout covers it; the server's own
    # startup hook then sees the models are loaded and skips.
    server.load_all()
    return server.app
