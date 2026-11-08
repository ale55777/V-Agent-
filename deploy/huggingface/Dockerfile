# EchoAI backend for a free Hugging Face Docker Space (2 vCPU, 16 GB RAM).
# Cloud mode: Groq does speech-to-text and replies; Silero (turn detection) and Piper (voice)
# run here. Whisper tiny is kept only as the fallback when Groq is unreachable.
FROM python:3.12-slim

RUN useradd -m -u 1000 user
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ECHOAI_MODELS_DIR=/app/models \
    ECHOAI_TTS_ENGINE=piper \
    ECHOAI_AI_ENGINE_OVERRIDE=groq \
    ECHOAI_STT_THREADS=2 \
    ECHOAI_TTS_THREADS=2 \
    HF_HOME=/app/models/hf

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=user . .
USER user
WORKDIR /app/backend
EXPOSE 7860
# Migrations are idempotent; running them on boot keeps the database schema in step with the code.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 7860 --log-level info --no-access-log"]
