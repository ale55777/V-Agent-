# EchoAI backend for the Docker Compose stack.
# Cloud-style: Groq does speech-to-text and replies; Silero (turn detection) and Piper (voice)
# run here. Whisper tiny is the fallback when Groq is unreachable. The models are not baked
# into the image: compose mounts .data/models, so they are not stored twice on disk.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ECHOAI_MODELS_DIR=/models \
    HF_HOME=/models/hf

WORKDIR /app
COPY deploy/huggingface/requirements.txt requirements.txt
RUN pip install -r requirements.txt
# Kokoro: the extra voices and the Urdu voice. It is loaded only when one of them is used,
# so it costs no RAM otherwise. Piper stays the default on this small CPU.
RUN pip install "kokoro-onnx>=0.4.9,<1"

COPY backend/app backend/app
COPY backend/alembic backend/alembic
COPY backend/alembic.ini backend/alembic.ini

WORKDIR /app/backend
EXPOSE 8000
# Migrations are idempotent; running them on boot keeps the schema in step with the code.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info --no-access-log"]
