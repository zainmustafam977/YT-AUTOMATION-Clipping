# Multi-stage Dockerfile for YouTube Shorts Automation Platform
# Includes FFmpeg 7+, libass, Python 3.11, and GPU/CPU inference dependencies.

FROM python:3.11-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libass-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy dependency specifications
COPY pyproject.toml ./
RUN uv pip install --system --no-cache \
    pydantic \
    pydantic-settings \
    cryptography \
    sqlalchemy \
    psycopg2-binary \
    opencv-python-headless \
    numpy \
    torch \
    faster-whisper \
    yt-dlp \
    google-genai \
    fastapi \
    uvicorn \
    typer \
    rich \
    pytest

# Final Production Stage
FROM python:3.11-slim AS runner

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install runtime ffmpeg with libass
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed python site-packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Create non-root application user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy application package and assets
COPY shorts_engine/ /app/shorts_engine/
COPY assets/ /app/assets/
COPY cli.py /app/cli.py
COPY run.py /app/run.py

# Create storage mounts with correct permissions
RUN mkdir -p /app/storage /app/storage/temp /app/storage/outputs /app/storage/downloads \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "cli.py", "status"]
