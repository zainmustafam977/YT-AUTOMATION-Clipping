# Operational Quality, Performance & Reliability Targets

This document establishes the measurable targets, architectural invariants, and non-negotiable service-level objectives (SLOs) for the **YouTube Shorts Automation Platform** as defined in Phase 0 of the industrial-grade review.

---

## 1. System Context & Operating Constraints

* **System Description:** End-to-end automated platform that ingests long-form video uploads, generates word-level transcripts, selects viral short-form segments via generator-critic LLM evaluation, reframes landscape footage to 9:16 portrait using neural face tracking, synthesizes word-synced karaoke subtitles, applies EBU R128 audio mastering, runs multi-tier quality gates, and orchestrates review and publishing with Bayesian A/B CTR optimization.
* **Stack & Runtime:**
  * **Language & Runtime:** Python 3.11, Windows 11 / Linux (Debian container).
  * **API & Dashboard:** FastAPI, Uvicorn, Jinja2, HTMX, Bootstrap 5.
  * **Database:** SQLAlchemy 2.0 ORM over SQLite in Write-Ahead Logging (`PRAGMA journal_mode=WAL;`) with full PostgreSQL schema portability.
  * **External Media Tools:** FFmpeg 9.0, `yt-dlp` (2026.08.19), OpenCV DNN with YuNet ONNX model.
  * **AI & LLM Services:** Groq LPU API (`openai/gpt-oss-120b`), Google Gemini API (`gemini-3.8-flash`), OpenRouter API (`meta-llama/llama-3.3-70b-instruct:free`), OpenAI API (`gpt-4o-mini`).
* **Scale Profile:**
  * **Current:** 1–5 watched channels, 3–10 source videos ingested daily, 15–60 generated Shorts/day.
  * **Target Scaling:** 20+ channels, 50+ long videos/day, 150–300 generated Shorts/day.
  * **Storage Envelope:** 50–200 GB active disk volume with automated fragment and cache lifecycle pruning.
* **Operational Constraints:**
  * **Budget:** Zero mandatory infrastructure spend. Operates on generous free-tier LLM pools with automatic cross-provider failover.
  * **Staffing:** Single operator / developer. Every operation must be observable and maintainable via web dashboard and CLI.
  * **Audit Mode:** *Audit, wait for approval, then implement P0+P1*.

---

## 2. Measurable Industrial-Grade Targets

| Dimension | Target Metric | Measurement Method | Failure Action |
| :--- | :--- | :--- | :--- |
| **Unattended Ingestion Success** | **$\ge 98.0\%$** of clean long videos process to completion without operator intervention | Track `status` transitions in `source_videos` table across last 100 jobs | Alert operator and route failed video to Dead-Letter Queue with full root cause diagnosis |
| **Automatic Self-Healing Time** | **$< 30.0$ seconds** mean time to recover (MTTR) from transient failures | Measure duration from transient error detection to successful retry | Trip circuit breaker and notify operator if backoff threshold exceeded |
| **Duplicate Work Prevention** | **$0$ duplicate ingestions** and **$0$ duplicate published shorts** | Unique database constraints on `youtube_video_id` and idempotent publish tokens | Rejection of duplicate detection events at ingestion entry point |
| **Data Loss Ceiling** | **$0$ lost in-flight jobs** on process crash or restart | State persistence on every phase transition (`DOWNLOADING`, `TRANSCRIBING`, `ANALYZING`, etc.) | Automatic reconciliation of `STALE` jobs on worker boot via `reconcile_stale_jobs()` |
| **LLM Inference Turnaround** | **$< 15.0$ seconds** latency for 10,000+ token transcripts | High-speed Groq LPU primary routing with Gemini 1M context fallback | Failover to secondary provider on HTTP 429 or $> 25\text{s}$ timeout |
| **Video Rendering Latency** | **$\le 1.0\times$ real-time** on CPU (e.g. 30s video renders in $\le 30$s); $< 0.25\times$ on GPU | Benchmark compositor execution time via `SubprocessWatchdog` | Automatic fallback from failed GPU encoder (`h264_nvenc`) to `libx264` |
| **Peak Memory Consumption** | **$< 2.5$ GB RSS** per worker process | Monitor resident set size during peak transcription and rendering | Explicit `gc.collect()` and temp buffer unlinking after each video |
| **Disk Hygiene & Leak Prevention** | **$0$ orphaned temporary files** older than 24 hours; $0$ partial download fragments older than 2 hours | Automated purge on worker startup and daily maintenance daemon | Automated filesystem sweep in `storage/temp` and `storage/downloads` |
| **Audio Mastering Compliance** | Integrated loudness at **$-14.0\text{ LUFS} \pm 1.0\text{ LUFS}$**, True Peak **$\le -1.5\text{ dBFS}$** | Dual-pass FFmpeg EBU R128 filter evaluation | Fatal technical quality gate rejection if out of compliance |
| **Safe-Zone Collision Rate** | **$0$ subtitle collisions** in right-rail interactive area ($X \ge 900\text{px}$) and bottom metadata ($Y \ge 1536\text{px}$) | Computer Vision 5-frame sample deadzone inspector | Automatic remediation via `caption_safe_rerender` adjusting `margin_vertical` |
| **Process Supervision & Leaks** | **$0$ orphaned zombie processes** (`ffmpeg`, `yt-dlp`) | Two-stage tree termination (`SIGTERM` $\to$ `SIGKILL`) via `psutil` | Guaranteed process tree kill and partial fragment cleanup on timeout |
