# N-ATLAS server

Serves the open N-ATLAS models to the AjaTrade WhatsApp bot:

| Model | Used for |
|---|---|
| `NCAIR1/N-ATLaS` (Llama-3 8B fine-tune) | translation, intent routing, fact extraction, diagnosis reasoning, farm-advice wording, memory summaries |
| `NCAIR1/Yoruba-ASR`, `NCAIR1/Hausa-ASR`, `NCAIR1/Igbo-ASR`, `NCAIR1/NigerianAccentedEnglish` (Whisper-small fine-tunes) | voice-note transcription, picked by the farmer's language (Pidgin uses Nigerian-accented English) |

The bot calls two endpoints with a shared secret:

- `POST /v1/chat/completions` (OpenAI-style; `response_format: json_object` is honoured by pre-filling `{`)
- `POST /asr` (multipart `file` + `language` = yoruba | hausa | igbo | english | pidgin | auto)
- `GET /health`

With `language=auto` (first voice note, language unknown) every ASR model transcribes the audio and the most confident one (highest mean token log-probability) wins. Audio longer than 28 s is cut into 28 s pieces because the ASR models take 30 s at most.

## Before deploying

1. Log in to huggingface.co and click **Agree and access** on all five models above (they are gated).
2. Create a **read** access token (Settings, Access Tokens).
3. Pick a long random secret for `NATLAS_SERVER_KEY`.

## Option A: Modal

`modal_app.py` runs the same server on a Modal L4 GPU that starts on demand and stops when idle. From the AjaTrade folder:

```bash
pip install modal
modal setup                                   # log in once (opens the browser)
modal secret create natlas-secrets HF_TOKEN=hf_... NATLAS_SERVER_KEY=<long-random-secret>
modal run natlas_server/modal_app.py::download   # one time: copies the models into a Modal volume
NATLAS_MIN_CONTAINERS=1 modal deploy natlas_server/modal_app.py
```

The deploy prints the URL (`https://<workspace>--ajatrade-natlas-serve.modal.run`). Check `<url>/health`.

- `NATLAS_MIN_CONTAINERS=1` keeps the GPU warm (about $0.80 an hour, so about $19 a day). Use it while farmers are testing; redeploy with `0` afterwards so it only runs when called. Modal's Starter plan includes $30 of credit a month.
- With `0`, the first message after a quiet period waits 1 to 3 minutes for the model to load. Set `NATLAS_TIMEOUT_SECONDS=240` on the bot in that case.
- Logs: `modal app logs ajatrade-natlas`.

## Option B: Hugging Face Space

1. New Space, SDK **Docker**, hardware **Nvidia L4** (24 GB). Public is fine: the server rejects calls without the secret.
2. Upload `Dockerfile`, `server.py` and `requirements.txt` from this folder to the Space.
3. Space settings, **Secrets**: `HF_TOKEN` (the token) and `NATLAS_SERVER_KEY` (the secret).
4. Set the sleep time to "never" while real farmers are using the bot (a cold start re-loads 17 GB).
5. The first build downloads the models (10 to 20 minutes). Open `https://<user>-<space>.hf.space/health`; it should list the LLM and four ASR models.

## Option C: RunPod / any GPU VM

```bash
docker build -t natlas-server natlas_server/
docker run --gpus all -p 8000:7860 -e HF_TOKEN=hf_... -e NATLAS_SERVER_KEY=... natlas-server
```

## Option D: free Kaggle or Colab GPU (testing only)

See the top of `run_in_notebook.py`. It loads the model in 4-bit (`NATLAS_LOAD_IN_4BIT=1`) so it fits a 16 GB T4, and prints a public `trycloudflare.com` URL. The session stops after some hours, so do not rely on it for real users.

## Connect the bot

Set these on Railway (the bot), then redeploy:

```
NATLAS_BASE_URL=https://<user>-<space>.hf.space
NATLAS_API_KEY=<the NATLAS_SERVER_KEY value>
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `HF_TOKEN` | none | token of an account that accepted the five model terms |
| `NATLAS_SERVER_KEY` | none | shared secret; without it the server is open (local tests only) |
| `NATLAS_MODEL` | `NCAIR1/N-ATLaS` | language model repo |
| `NATLAS_LOAD_IN_4BIT` | `0` | `1` for 16 GB GPUs |
| `NATLAS_ASR_LANGS` | all four | comma list to load fewer ASR models |
| `NATLAS_ASR_DEVICE` | `cuda` if present | `cpu` keeps the GPU for the LLM |

Generation follows the model card: Llama-3 chat template with `date_string`, repetition penalty 1.12, low temperature. One generation runs at a time per GPU; further requests queue.

## Licence

N-ATLaS and the ASR models are released under the N-ATLaS Open-Source Research and Innovation License: research, education and civic use, up to 1,000 monthly active users, with attribution ("Powered by Awarri"). Larger or commercial deployments need a licence from Awarri.
