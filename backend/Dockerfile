# MarksWala backend (API and worker share this image)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# test-only packages are not needed at runtime
RUN grep -v -E '^(pytest|httpx|ruff)' requirements.txt > runtime-requirements.txt && pip install -r runtime-requirements.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 marks && mkdir -p /data/storage && chown -R marks /data /app
USER marks
ENV STORAGE_LOCAL_ROOT=/data/storage
EXPOSE 8000
# API: apply migrations, then serve.  Worker: override the command (see docker-compose.yml).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
