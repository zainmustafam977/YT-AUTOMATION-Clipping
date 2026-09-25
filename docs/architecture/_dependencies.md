# System Dependencies & Third-Party Integrations

> **As-Implemented Dependency Ledger**  
> Extracted from `requirements.txt`, `requirements-dev.txt`, and `pyproject.toml`  
> Verified in code imports across the entire repository.

## Production & Development Dependencies

| Package Name | Version Spec | Scope | Role in Architecture |
| :--- | :--- | :--- | :--- |
| **`fastapi`** | `>=0.110.0,<1.0.0` | `Production` | FastAPI web framework hosting the dashboard UI, SSE telemetry stream, and health/metrics endpoints. |
| **`uvicorn[standard]`** | `>=0.28.0,<1.0.0` | `Production` | High-performance ASGI server for hosting FastAPI in asynchronous event loop. |
| **`starlette`** | `>=0.36.0,<1.0.0` | `Production` | Underlying ASGI toolkit for FastAPI providing request, response, routing, and session middleware. |
| **`jinja2`** | `>=3.1.2,<4.0.0` | `Production` | Templating engine rendering server-side dashboard HTML templates (status, queue, channels, settings). |
| **`python-multipart`** | `>=0.0.9` | `Production` | Streaming multipart form parser supporting file uploads and dashboard form submissions. |
| **`httpx`** | `>=0.27.0,<1.0.0` | `Production` | Next-generation HTTP client with async/sync support for downloading media and calling REST APIs. |
| **`pydantic`** | `>=2.6.0,<3.0.0` | `Production` | Data validation, strict typing, and model serialization for clips, candidates, and pipeline states. |
| **`pydantic-settings`** | `>=2.2.0,<3.0.0` | `Production` | Environment variable loader reading .env files into typed Settings singleton with fallback bounds. |
| **`sqlalchemy`** | `>=2.0.25,<3.0.0` | `Production` | Object-Relational Mapping (ORM) and SQL generation for SQLite/PostgreSQL persistence. |
| **`aiosqlite`** | `>=0.20.0` | `Production` | Asynchronous SQLite driver for non-blocking database queries in async event loops. |
| **`asyncpg`** | `>=0.29.0` | `Production` | High-performance asynchronous PostgreSQL driver for enterprise multi-worker scale. |
| **`psycopg2-binary`** | `>=2.9.9` | `Production` | Synchronous PostgreSQL adapter supporting DB migrations and sync ORM operations. |
| **`cryptography`** | `>=42.0.0` | `Production` | Fernet symmetric encryption engine for securing channel OAuth tokens and sensitive credentials at rest. |
| **`google-genai`** | `>=1.0.0` | `Production` | Official Google GenAI SDK for Gemini 2.0 Flash / Pro transcript analysis and multimodal evaluation. |
| **`google-api-python-client`** | `>=2.115.0` | `Production` | Official Google API client for YouTube Data API v3 clip metadata insertion and uploads. |
| **`google-auth-oauthlib`** | `>=1.2.0` | `Production` | OAuth 2.0 client flow library for authenticating YouTube channel permissions. |
| **`google-auth-httplib2`** | `>=0.2.0` | `Production` | HTTP transport adapter for Google API authentication tokens. |
| **`google-auth`** | `>=2.27.0,<3.0.0` | `Production` | Core Google identity and authentication library. |
| **`yt-dlp`** | `>=2024.3.10` | `Production` | Modular YouTube downloader extracting video/audio streams, subtitles, and channel metadata. |
| **`faster-whisper`** | `>=1.0.1` | `Production` | CTranslate2-accelerated Whisper transcription engine providing word-level timestamps and VAD filtering. |
| **`torch`** | `>=2.1.0` | `Production` | Deep learning runtime required by PyTorch models (Silero VAD and Whisper transcription). |
| **`numpy`** | `>=1.24.0,<2.0.0` | `Production` | Numerical matrix operations for audio normalization, image cropping calculations, and video buffers. |
| **`opencv-python-headless`** | `>=4.9.0.80` | `Production` | Computer vision library for smart face/speaker detection, saliency tracking, and aspect framing. |
| **`pillow`** | `>=10.2.0` | `Production` | Python Imaging Library for image manipulation, thumbnail compositing, and font rendering. |
| **`scikit-learn`** | `>=1.4.0` | `Production` | Clustering and statistical algorithms for smoothing crop trajectory keypoints. |
| **`typer`** | `>=0.9.0,<1.0.0` | `Production` | Type-driven CLI framework creating the multi-command engine interface (`run.py` / `cli.py`). |
| **`rich`** | `>=13.7.0` | `Production` | Terminal UI library rendering rich tables, progress bars, colored logs, and interactive consoles. |
| **`sentry-sdk`** | `>=1.40.0` | `Production` | Application performance monitoring (APM) and centralized exception reporting. |
| **`psutil`** | `>=5.9.8` | `Production` | Cross-platform process supervisor discovering child PIDs, terminating trees, and reading host RAM/CPU. |
| **`pytest`** | `>=8.0.0` | `Development` | Test execution harness running 266 unit, integration, and architecture tests. |
| **`pytest-asyncio`** | `>=0.23.0` | `Development` | Pytest plugin supporting asynchronous test coroutines and async DB engines. |
| **`pytest-cov`** | `>=4.1.0` | `Development` | Test coverage measurement tool computing line and branch test coverage. |
| **`playwright`** | `>=1.40.0` | `Development` | Headless browser automation tool for end-to-end dashboard and UI verification. |
| **`ruff`** | `>=0.3.0` | `Development` | Ultra-fast Python linter and code formatter checking syntax, style, and security rules. |
| **`mypy`** | `>=1.8.0` | `Development` | Static type checker verifying Python type annotations across engine modules. |
| **`types-psutil`** | `>=5.9.0` | `Development` | Type stubs for psutil library. |
| **`types-pillow`** | `>=10.2.0` | `Development` | Type stubs for Pillow library. |

---

## External System & Runtime Binaries

The system relies on external platform binaries installed on the host OS or Docker container:

| Binary Tool | Verification Method | Role in System | Minimum Recommended Version |
| :--- | :--- | :--- | :--- |
| **`ffmpeg`** | `ffmpeg -version` (verified in `readyz` probe) | Core audio/video compositing, filtering (crop, scale, overlay, ducking, ass subtitles), audio mastering, and hardware-accelerated video encoding (NVENC, QSV, AMF, libx264). | 6.0+ (7.0+ recommended) |
| **`ffprobe`** | `ffprobe -version` (verified in `readyz` probe) | Audio/video stream metadata inspection, duration extraction, resolution check, SAR check, and post-render QA gate inspection. | 6.0+ |
| **`caddy`** | `caddy version` (referenced in `Caddyfile`) | Reverse proxy with automated TLS termination and static file delivery. | 2.7+ |
| **`sqlite3` / `postgres`** | Embedded `sqlite3` / asyncpg client | Relational state persistence with WAL mode or ACID PostgreSQL clustering. | SQLite 3.35+ / PostgreSQL 14+ |

---

## External Cloud APIs & Third-Party Services

| Service Provider | Protocol / SDK | Purpose & Invariant | Where Managed |
| :--- | :--- | :--- | :--- |
| **YouTube Data API v3** | Google REST API (`google-api-python-client`) | Uploads finalized shorts, sets metadata/tags/descriptions, and polls performance metrics. Hard-capped at 10,000 units/day via `QuotaTracker`. | `shorts_engine/services/publishing/client.py`, `quota.py` |
| **YouTube RSS Feeds** | HTTP GET XML parsing | Real-time discovery of newly published source videos on monitored channels without consuming API quota units. | `shorts_engine/services/detection/rss_poller.py` |
| **Groq Cloud API** | HTTP REST (OpenAI-compatible) | Ultra-fast LPU inference (`llama-3.3-70b-versatile`, `mixtral-8x7b-32768`) for hook generation, boundary detection, and virality scoring. | `shorts_engine/services/llm/router.py` |
| **Google Gemini API** | Google GenAI SDK (`google-genai`) | Multimodal evaluation and deep reasoning (`gemini-2.0-flash`, `gemini-1.5-pro`) for transcript scoring and semantic quality checks. | `shorts_engine/services/llm/router.py` |
| **OpenRouter API** | HTTP REST (OpenAI-compatible) | Secondary fallback provider for open-weights models (`deepseek-r1`, `claude-3.5-sonnet`) when primary quotas are exhausted. | `shorts_engine/services/llm/router.py` |
| **TikTok Open API** | Direct HTTP REST multipart upload | Multi-platform syndication of approved clips to TikTok user accounts. | `shorts_engine/services/publishing/tiktok.py` |
| **Instagram Graph API** | Direct HTTP REST container upload | Multi-platform syndication of approved clips to Instagram Reels via Facebook Graph API. | `shorts_engine/services/publishing/instagram.py` |
| **Telegram Bot API** | Direct HTTP POST alerts | Real-time push notifications for critical errors, quota exhaustion, and clip approvals. | `shorts_engine/services/monitoring/telegram.py` |
| **Sentry APM** | Sentry SDK (`sentry-sdk`) | Application error monitoring, unhandled exception captures, and distributed tracing. | `shorts_engine/services/monitoring/sentry.py` |