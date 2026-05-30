# Multi-stage Dockerfile for the IPO scraper.
#
# Image goals:
#   - Small final image (~150–200 MB) — only runtime deps in layer 2.
#   - Memory-tight at runtime (~150 MB idle, ~350 MB during PDF resolve).
#   - Same image works on Fly.io, Railway, and any VPS / docker-compose host.
#
# Build:  docker build -t ipo-scraper .
# Run:    docker run --env-file .env -p 8001:8001 ipo-scraper

# ─── Stage 1: builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Minimal build tooling. PyMuPDF / pdfplumber / lxml need gcc + dev headers.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt


# ─── Stage 2: runtime ───────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime-only system libs. PyMuPDF needs libstdc++ at runtime; pillow may
# pull in libjpeg/libopenjp2 transitively. Keep this list minimal.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 libgomp1 ca-certificates curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Memory + Python ergonomics:
#   MALLOC_ARENA_MAX=2  → reins in glibc's per-thread arena fragmentation
#                        (default is 8 × 64MB on 64-bit; uses far less RAM).
#   PYTHONUNBUFFERED    → flush stdout immediately so platform log tailing works.
#   PYTHONDONTWRITEBYTECODE → no __pycache__ littering inside the image layer.
ENV MALLOC_ARENA_MAX=2 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PORT=8001 \
    LOG_LEVEL=INFO

COPY --from=builder /install /usr/local

WORKDIR /app

# Copy only what runs in production. Anything in .dockerignore is excluded.
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY README.md ./

# Run as a non-root user — best practice; also prevents anyone who pops
# a shell in the container from owning the whole filesystem.
RUN useradd -r -u 1000 -m -d /home/app app && \
    chown -R app:app /app
USER app

EXPOSE 8001

# Liveness check used by Fly / Railway — fast, no external deps.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8001}/health" || exit 1

# Single uvicorn worker is fine for our workload (low QPS, mostly background
# tasks). Multiple workers would mean multiple in-memory task caches.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001} --workers 1 --proxy-headers --forwarded-allow-ips=*"]
