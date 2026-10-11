# Project Pending Tasks & Engineering Roadmap

This document maintains the technical backlog and upcoming implementation packages for the YouTube Shorts Automation pipeline (`yt-automation-clipping`).

---

## 📋 Upcoming Packages Overview

| Package | Status | Priority | Core Focus |
| :--- | :--- | :--- | :--- |
| **Package 1: Durable Job Queue Architecture** | ✅ Completed | High | Transactional task queue, exponential backoff, DLQ, and crash recovery |
| **Package 2: Multi-Worker Concurrency & Scaling** | ✅ Completed | High | PostgreSQL migration engine, dialect-aware DDL, and multi-node claim isolation |
| **Package 3: Pipeline AI & Media Enhancements** | ⏳ Backlog | Medium | Dynamic subtitle styling, smart reframing, and automated A/B testing |
| **Package 4: Observability & Health Dashboards** | ⏳ Backlog | Medium | Centralized metrics, Prometheus/Grafana or FastAPI real-time monitor |

---

## 🚀 Detailed Package Specifications

### Package 1: Durable Job Queue Architecture & Redesign
- **Current State**:
  - In-flight execution progress telemetry uses `JobProgressTracker` in process memory.
  - While state (candidates, clips, render paths, publish logs) is committed to SQLite, pipeline stage transitions (ingest &rarr; transcribe &rarr; clip &rarr; reframe &rarr; caption &rarr; review &rarr; publish) rely on sequential loops and orchestrator state.
- **Goals & Requirements**:
  1. **Persistent Task Queue**:
     - Implement a durable job queue that survives process crashes and reboots.
     - Option A: Database-backed queue with row locking (`FOR UPDATE SKIP LOCKED` on Postgres or atomic leases in SQLite).
     - Option B: Redis-backed queue (`RQ`, `Celery`, or `ARQ`) for high-throughput task dispatch.
  2. **Failure Handling & Retries**:
     - Configurable retry policies with exponential backoff for transient failures (e.g. YouTube download rate limits, LLM rate limits).
     - Dead-Letter Queue (DLQ) for poisoned or non-recoverable jobs.
  3. **Idempotency & Stage Resumption**:
     - Re-running a failed or aborted job must resume from the last completed stage artifact without re-downloading source video or re-transcribing.
  4. **Real-time Event Broadcasting**:
     - Stream task state changes and progress percentages over WebSocket/SSE to the frontend review dashboard.

---

### Package 2: Multi-Worker Concurrency & Database Scaling Decision
- **Current State**:
  - The database runs SQLite at `storage/shorts_automation.db` with WAL mode enabled (`journal_mode=wal`, `busy_timeout=30000`).
  - Concurrent reads are fully supported across the dashboard and worker processes.
  - Concurrent writes are serialized by SQLite; multi-process writers can face table/database lock contention during heavy rendering/transcription batches.
- **Architectural Decision Needed**:
  - **Option A (Stay on SQLite)**:
    - Target: Single-node server or low-resource VPS.
    - Architecture: Single background worker process (or dedicated write-daemon with worker threads) to eliminate multi-process write contention.
  - **Option B (Migrate to PostgreSQL)**:
    - Target: Multi-node cluster with parallel GPU render workers.
    - Architecture: Full migration of `storage/shorts_automation.db` to PostgreSQL. Enables multiple independent worker instances running concurrently across distinct hosts.
- **Tasks**:
  1. Operator approval on Option A vs Option B based on deployment scale.
  2. Implement worker heartbeat registry (`worker_nodes` table) to monitor worker liveness and auto-reassign orphaned tasks.
  3. Benchmark database write latency under high-load synthetic clipping batches.

---

### Package 3: Pipeline AI & Video Rendering Enhancements
- **Goals & Requirements**:
  1. **Smart Speaker Reframing**:
     - Enhance 9:16 vertical video reframing with face detection & active speaker tracking (YOLO / MediaPipe).
     - Support dynamic multi-speaker split-screen layouts for podcast/interview source material.
  2. **Dynamic Animated Captions**:
     - Extend FFmpeg / ASS caption renderer to support word-by-word highlight animations, karaoke-style timing, and customizable font packs.
     - Pre-tested styling presets (e.g. "Hormozi style", "Minimal clean", "Neon punch").
  3. **Autonomous A/B Variant Generation**:
     - Auto-generate 2-3 title and thumbnail variants per approved clip.
     - Integrate YouTube Data API v3 analytics ingestion to measure real-world click-through rate (CTR) and average view duration (AVD).

---

### Package 4: System Observability & Monitoring
- **Goals & Requirements**:
  1. **Structured Metrics**:
     - Measure API quota utilization against YouTube daily cap (10,000 units/day) with proactive alerts at 85% usage.
     - Track AI token costs per clip (Groq, Gemini, OpenRouter, OpenAI).
  2. **Health Endpoints**:
     - Standardize `/api/health` and `/api/readiness` endpoints checking DB connectivity, disk space (`storage/`), and FFmpeg binary availability.

---

## 📌 Related Documentation & Checklists

- [**PENDING_OPERATOR_TASKS.md**](file:///d:/Projects/YT%20AUTOMATION%20CLIPPING/YT%20AUTOMATION/PENDING_OPERATOR_TASKS.md) &mdash; Manual security cleanup tasks for the operator (backup file deletion, upstream key revocation).
- [**docs/SECRETS.md**](file:///d:/Projects/YT%20AUTOMATION%20CLIPPING/YT%20AUTOMATION/docs/SECRETS.md) &mdash; Master key encryption architecture, key generation, and rotation guide.
- [**docs/PERSISTENCE.md**](file:///d:/Projects/YT%20AUTOMATION%20CLIPPING/YT%20AUTOMATION/docs/PERSISTENCE.md) &mdash; Database schema mapping, SQLite WAL configuration, and process lifecycle survival matrix.
