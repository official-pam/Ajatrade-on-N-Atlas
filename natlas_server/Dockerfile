# GPU image for the N-ATLAS server (Hugging Face Space "Docker", RunPod, etc.)
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

# Hugging Face Spaces expect port 7860; RunPod/Modal can override PORT.
ENV PORT=7860 HF_HOME=/tmp/hf
EXPOSE 7860
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
