"""Run the N-ATLAS server from a free Kaggle or Colab GPU notebook.

Free, no card needed. A Kaggle session lasts at most 12 hours (about 30 GPU
hours a week), so restart it when it stops.

Kaggle secrets (Add-ons -> Secrets): HF_TOKEN, NATLAS_SERVER_KEY, and for a
fixed address NGROK_AUTHTOKEN and NGROK_DOMAIN (free ngrok account ->
Domains). Then in notebook cells (GPU T4 x2, Internet on):

    !git clone https://github.com/official-pam/Ajatrade-on-N-Atlas.git
    %cd Ajatrade-on-N-Atlas/natlas_server

    import os
    from kaggle_secrets import UserSecretsClient
    s = UserSecretsClient()
    for name in ("HF_TOKEN", "NATLAS_SERVER_KEY", "NGROK_AUTHTOKEN", "NGROK_DOMAIN"):
        try:
            os.environ[name] = s.get_secret(name)
        except Exception:
            pass
    os.environ["NATLAS_LOAD_IN_4BIT"] = "1"

    !python run_in_notebook.py

It installs the server, loads the models and prints the public URL to put in
the bot's NATLAS_BASE_URL. With the ngrok secrets the URL is the same every
session; without them a random trycloudflare.com URL is printed each time.
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
NGROK = "/tmp/ngrok"
NGROK_URL = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz"


def announce(url: str) -> None:
    print("\n" + "=" * 60)
    print("NATLAS_BASE_URL =", url)
    print("=" * 60 + "\nKeep this cell running.")


def start_ngrok(token: str, domain: str) -> subprocess.Popen | None:
    """Fixed address on the free ngrok plan (one free static domain)."""
    domain = domain.removeprefix("https://").removeprefix("http://").strip("/")
    if not os.path.exists(NGROK):
        urllib.request.urlretrieve(NGROK_URL, "/tmp/ngrok.tgz")
        subprocess.run(["tar", "-xzf", "/tmp/ngrok.tgz", "-C", "/tmp"], check=True)
    subprocess.run([NGROK, "config", "add-authtoken", token], check=True, capture_output=True)
    for flag in (f"--url=https://{domain}", f"--domain={domain}"):
        tunnel = subprocess.Popen([NGROK, "http", str(PORT), flag, "--log=stdout"], stdout=subprocess.DEVNULL)
        time.sleep(6)
        if tunnel.poll() is None:
            announce(f"https://{domain}")
            return tunnel
    print("ngrok did not start (check NGROK_AUTHTOKEN / NGROK_DOMAIN); using a temporary address instead.")
    return None


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

    token, domain = os.getenv("NGROK_AUTHTOKEN", ""), os.getenv("NGROK_DOMAIN", "")
    if token and domain and start_ngrok(token, domain):
        server.wait()
        return

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
            announce(match.group(0))
            break
    server.wait()


if __name__ == "__main__":
    main()
