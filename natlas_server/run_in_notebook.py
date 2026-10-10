"""Run the N-ATLAS server from a free Kaggle or Colab GPU notebook.

Free option for demos and testing. The notebook session ends after some hours
of use, so for the real-user validation period prefer a Hugging Face Space or
RunPod (see natlas_server/README.md).

In a notebook cell (GPU on, internet on):

    !git clone https://github.com/Seun-Agba-coder/AjaTrade.git
    %cd AjaTrade/natlas_server
    import os
    os.environ["HF_TOKEN"] = "hf_..."             # account that accepted the terms
    os.environ["NATLAS_SERVER_KEY"] = "pick-a-long-random-secret"
    os.environ["NATLAS_LOAD_IN_4BIT"] = "1"        # T4 has 16 GB
    !python run_in_notebook.py

It installs the server, starts it, opens a Cloudflare quick tunnel and prints
the public URL. Put that URL in the bot's NATLAS_BASE_URL.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.request

PORT = 8000
CLOUDFLARED = "/tmp/cloudflared"
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
)


def main() -> None:
    if not os.getenv("HF_TOKEN"):
        sys.exit("Set HF_TOKEN first (the N-ATLAS models are gated).")
    if not os.getenv("NATLAS_SERVER_KEY"):
        sys.exit("Set NATLAS_SERVER_KEY first so the server is not open to everyone.")

    subprocess.run("apt-get -qq install -y ffmpeg > /dev/null 2>&1 || true", shell=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=True)

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", str(PORT)]
    )
    print("Loading models (first run downloads about 17 GB, 5-15 minutes)...")
    while True:
        if server.poll() is not None:
            sys.exit("Server stopped while loading; see the log above.")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                print("Server ready:", r.read().decode())
                break
        except Exception:
            time.sleep(10)

    if not os.path.exists(CLOUDFLARED):
        urllib.request.urlretrieve(CLOUDFLARED_URL, CLOUDFLARED)
        os.chmod(CLOUDFLARED, 0o755)
    tunnel = subprocess.Popen(
        [CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for line in tunnel.stdout:  # type: ignore[union-attr]
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if match:
            print("\n" + "=" * 60)
            print("NATLAS_BASE_URL =", match.group(0))
            print("=" * 60 + "\nKeep this cell running.")
            break
    server.wait()


if __name__ == "__main__":
    main()
