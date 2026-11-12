# Multi-stage Dockerfile for YouTube Shorts Automation Platform
# Includes FFmpeg 7+, libass, Python 3.11, and GPU/CPU inference dependencies.

FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libass-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for ultra-fast dependency management
COPY --from=ghcr.io/astral-sh/uv@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /bin/uv

WORKDIR /app

# Create self-contained virtualenv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
# Install lightweight CPU-targeted PyTorch first to prevent CUDA wheel bloat, then all production dependencies
RUN uv venv /opt/venv && \
    uv pip install --no-cache --extra-index-url https://download.pytorch.org/whl/cpu torch && \
    uv pip install --no-cache -r requirements.txt pytest

# Final Production Stage
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS runner

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Install runtime ffmpeg with libass
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libass9 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed python virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

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
