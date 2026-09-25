# 00 System Overview & Executive Summary

**System Name:** YouTube Shorts Automation Platform ("Shorts Engine")  
**Repository:** `d:\Projects\YT AUTOMATION`  
**Architecture Document Version:** 1.0.0  
**Verification Date:** 2026-09-24  
**Implementation Status:** Production-Ready (266/266 Automated Unit/Integration Tests Passing)

---

## 1. Executive Summary

The **YouTube Shorts Automation Platform** is an enterprise-grade, autonomous media intelligence and syndication pipeline designed to ingest long-form video content, perform speech transcription and forced alignment, select viral candidate segments via multi-provider generative AI, dynamically track and crop active speakers to vertical 9:16 aspect ratio, generate kinetic karaoke subtitles, master broadcast-grade audio, execute automated multi-gate quality verification, and manage scheduled multi-platform syndication across YouTube Shorts, TikTok, and Instagram Reels.

The system is constructed with strict fault isolation, dialect-aware concurrency control, hardware encoder auto-detection with automatic software fallback, and process tree termination to prevent orphaned OS processes under heavy rendering workloads.

```mermaid
flowchart LR
    subgraph Ingestion ["1. Ingestion"]
        YT["YouTube / WebSub / RSS"] --> DL["Downloader & Audio Extractor"]
    end

    subgraph Intelligence ["2. AI & Audio Intelligence"]
        DL --> TR["Faster-Whisper / Groq Transcription"]
        TR --> LLM["Multi-Tier LLM Router\n(Groq / Gemini / Heuristic)"]
        LLM --> AM["Audio Mastering & Dynamic Ducking\n(EBU R128 -14 LUFS)"]
    end

    subgraph VisionRender ["3. Vision & Rendering"]
        AM --> CV["YuNet Face Tracking & Cropper\n(9:16 Portrait Canvas)"]
        CV --> CAP["Kinetic Karaoke Captioning\n(Hex ASS & Contrast)"]
        CAP --> COMP["Compositor Engine\n(NVENC / QSV / libx264)"]
    end

    subgraph GatesSyndication ["4. QA & Syndication"]
        COMP --> QA["4-Tier Quality Gates\n(Fatal, Black/Mute, CV, Semantic)"]
        QA --> DASH["FastAPI Review Queue & Dashboard\n(RBAC & CSRF Protection)"]
        DASH --> PUB["Multi-Platform Syndication\n(YouTube / TikTok / Reels)"]
        PUB --> BANDIT["Bayesian A/B Title Bandit\n(Closed-Loop Optimizer)"]
    end
```

---

## 2. Core Technology Stack

| Layer | Component / Technology | Specification & Implementation |
| :--- | :--- | :--- |
| **Language & Runtime** | Python 3.11+ | Strict typing, Pydantic v2 settings, concurrent thread pools |
| **Web & API Framework** | FastAPI / Starlette | Uvicorn ASGI server, Server-Sent Events (SSE), Jinja2 HTML5 |
| **Database & Persistence** | SQLAlchemy 2.0 ORM | Dual dialect support: SQLite 3 (WAL mode) / PostgreSQL 16 (`QueuePool`) |
| **Audio/Video Binaries** | FFmpeg 6.0+ / 9.0 build | Sub-frame trimming, EBU R128 `loudnorm`, `agate` sidechain ducking, hardware encoding |
| **Speech & Transcription**| Faster-Whisper / Groq Cloud | CTranslate2 inference (`whisper-large-v3-turbo` / `base`), Silero VAD |
| **Computer Vision** | OpenCV Headless (cv2) | YuNet ONNX face detection (`face_detection_yunet_2023mar.onnx`), active speaker hysteresis |
| **Generative AI** | Multi-Provider Router | Groq LPU (`gpt-oss-120b`), Google Gemini (`gemini-3.8-flash`), OpenRouter, OpenAI, Heuristic fallback |
| **Reverse Proxy & Edge** | Caddy 2.7+ | Automatic TLS, reverse proxy on ports 80/443, static asset compression |
| **Process Supervision** | SubprocessWatchdog | Tree termination via `taskkill /F /T` (Windows) and `os.killpg` (POSIX) |
| **Security & Cryptography**| Cryptography & Passlib | AES-256-GCM envelope encryption, PBKDF2-HMAC-SHA256 (600k rounds) |

---

## 3. Seven Architectural Tiers

The codebase is organized into seven strictly decoupled architectural tiers ([`02_component_architecture.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/02_component_architecture.md)):

1. **Foundations & Configuration:** Single source of truth settings (`settings.py`), logging setup, process watchdog (`watchdog.py`), and atomic backup manager.
2. **Persistence & Data Model:** Relational models with cascade rules (`models.py`), session factories, and path slugification budgeting under Windows `MAX_PATH` (`path_manager.py`).
3. **Ingestion & Detection:** yt-dlp client rotators, anti-bot mitigation, YouTube RSS Atom poller, and HMAC-SHA1 verified WebSub push subscriber.
4. **Perception, AI & Audio Mastering:** Faster-Whisper transcription, Groq/Gemini candidate selector, EBU R128 audio mastering, high-pass 80Hz rumble filter, and dynamic sidechain ducking.
5. **Computer Vision & Video Rendering:** YuNet face tracking, speaker motion hysteresis, 9:16 portrait cropper, ASS kinetic karaoke generator, and hardware/software video compositor.
6. **Quality Assurance & Verification:** Four fatal/non-fatal gates checking container validity, black frame clusters, audio silence, safe-zone caption collisions, and semantic alignment.
7. **Syndication, Optimization & Control:** YouTube Data API v3 publisher, TikTok API v2, Instagram Reels API v19.0, Bayesian multi-armed bandit title optimizer, and FastAPI operator dashboard.

---

## 4. Key Architectural Invariants & Innovations

1. **Hardware-to-CPU Encoder Failover:** The rendering engine performs an automated 1-frame probe of hardware encoders (`h264_nvenc`, `h264_qsv`, `h264_amf`). If hardware acceleration fails or encounters driver errors, it seamlessly falls back to software `libx264` and permanently caches the decision for the process session.
2. **Process Tree Watchdog:** Any external FFmpeg, yt-dlp, or Whisper subprocess is monitored with hard timeouts. Upon timeout or user cancellation, the engine terminates the entire operating system process tree, guaranteeing zero orphaned zombie encoding threads.
3. **Multi-User RBAC & CSRF Protection:** Legacy basic authentication has been completely removed. The system operates with PBKDF2 hashed credentials, signed HMAC session tokens (`<user_id>.<timestamp>.<hmac>`), role tiers (`admin`, `operator`, `viewer`), and cryptographic CSRF token validation on all mutating HTTP routes.
4. **Bayesian Bandit with Emergency Circuit Breaker:** Syndicated video titles undergo live Bayesian A/B optimization ($P(B > A) \ge 0.95$, $\ge 100$ impressions). If an hourly view velocity drop $>50\%$ occurs on the promoted variant, an automated circuit breaker immediately rolls back the title on YouTube.
5. **Universal Failure Recovery:** Rejected or failed clips feature 1-click diagnostic auto-fixes: duration clamping for over-length clips, caption margin shifting for UI dead-zone collisions, and transcript re-anchoring for alignment drift.

---

## 5. Architectural Documentation Map

| Document | Purpose |
| :--- | :--- |
| [`00_overview.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/00_overview.md) | Executive summary, tech stack, and architectural roadmap |
| [`01_system_context.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/01_system_context.md) | System boundary, human actors, external services and APIs |
| [`02_component_architecture.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/02_component_architecture.md) | Tiered component diagram, module coupling, and circular dependency breakages |
| [`03_runtime_and_deployment.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/03_runtime_and_deployment.md) | Process models, port allocations, Docker setup, Caddy reverse proxy, startup order |
| [`04_data_architecture.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/04_data_architecture.md) | Full relational DDL schemas, filesystem hierarchy, and JSON serialization contracts |
| [`05_flows_and_logic.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/05_flows_and_logic.md) | Sequence diagrams: Ingestion pipeline, SSE telemetry, universal recovery, A/B testing |
| [`06_api_and_interfaces.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/06_api_and_interfaces.md) | Exhaustive REST endpoint catalog, SSE streams, CLI commands, and batch scripts |
| [`07_configuration.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/07_configuration.md) | Configuration parameter reference, validation bounds, and hot-reload rules |
| [`08_security_and_errors.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/08_security_and_errors.md) | Threat model, AES-256-GCM envelope encryption, RBAC, CSRF, and failure recovery |
| [`09_module_reference/`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/09_module_reference/) | 16 deep module reference documents covering all 99 Python service modules |
| [`10_discrepancies.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/10_discrepancies.md) | Ground-truth audit of code vs documentation, dead code, and technical debt |
| [`11_coverage_and_gaps.md`](file:///d:/Projects/YT%20AUTOMATION/docs/architecture/11_coverage_and_gaps.md) | Complete file coverage ledger (210/210 files analyzed) and verification spot-checks |
