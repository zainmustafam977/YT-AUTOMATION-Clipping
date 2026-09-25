# Execution Flows & Pipeline Logic

> **Path:** `docs/architecture/05_flows_and_logic.md`  
> **Source Files:**
> - [`shorts_engine/orchestration/runner.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/orchestration/runner.py)
> - [`shorts_engine/dashboard/routes/status.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/dashboard/routes/status.py)
> - [`shorts_engine/services/quality_gate/recovery.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/quality_gate/recovery.py)
> - [`shorts_engine/services/ab_testing/winner_daemon.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/winner_daemon.py)

---

## 1. Flow 1: End-to-End Autonomous Pipeline

The core autonomous pipeline transforms an incoming YouTube video alert into fully rendered, mastered vertical clips ready for operator review or automated syndication:

```mermaid
sequenceDiagram
    autonumber
    actor YT as YouTube Hub / Feed
    participant DET as DetectionService
    participant RUN as PipelineRunner
    participant ING as IngestionService
    participant ASR as TranscriptionService
    participant SEL as ClipSelectionService
    participant WORKER as WorkerRunner / Compositor
    participant QG as QualityGateManager
    participant DB as Relational Database

    YT->>DET: WebSub Push Notification / RSS Poll
    DET->>DB: Check idempotency & create SourceVideo(status=DETECTED)
    DET->>RUN: Trigger pipeline execution
    RUN->>DB: SourceVideo: status = DOWNLOADING
    RUN->>ING: download_video(video_id)
    ING->>ING: Rotate player client & extract 16kHz mono WAV
    ING-->>RUN: VideoDownloadResult(video_path, audio_path)
    
    RUN->>DB: SourceVideo: status = TRANSCRIBING
    RUN->>ASR: transcribe(audio_path)
    ASR->>ASR: Faster-Whisper / Groq Whisper + ForcedAligner
    ASR-->>RUN: FormattedTranscript(words, silences)
    RUN->>DB: Store Transcript record in DB
    
    RUN->>DB: SourceVideo: status = ANALYZING
    RUN->>SEL: select_clips(transcript, bounds, overrides)
    SEL->>SEL: LLMRouter structured extraction + 4-tier boundary snap
    SEL-->>RUN: List of SelectedClip objects
    RUN->>DB: Create ClipCandidate & Clip(status=RENDERING)
    RUN->>DB: SourceVideo: status = SEGMENTS_SELECTED
    
    RUN->>WORKER: Render clips (direct or background worker queue)
    WORKER->>WORKER: VideoReframer active-speaker crop + ASSSubtitleGenerator
    WORKER->>WORKER: FFmpeg Compositor (Lanczos, sidechain ducking, loudnorm)
    WORKER-->>RUN: Rendered MP4 path
    
    RUN->>QG: evaluate_clip(rendered_mp4, transcript, hook, payoff)
    QG->>QG: Technical check + Black frame/silence + CV safe zone + Semantic
    alt Passed with Human Review Enabled
        QG-->>DB: Clip: status = WAITING_APPROVAL (with advisories)
    else Passed in Unattended Mode
        QG-->>DB: Clip: status = APPROVED
    else Fatal Quality Failure
        QG-->>DB: Clip: status = REJECTED
    end
    RUN->>DB: SourceVideo: status = COMPLETED
```

---

## 2. Flow 2: Manual Video Ingestion & Real-Time Telemetry

When an operator manually inputs a YouTube URL or video ID into the dashboard, the system conducts pre-flight validation, allows per-video runtime overrides, and streams live progress via Server-Sent Events (SSE):

```mermaid
sequenceDiagram
    autonumber
    actor OP as Dashboard Operator
    participant UI as Dashboard Frontend (status.html)
    participant API as routes/status.py (/ingest)
    participant SSE as routes/status.py (/events)
    participant TRK as job_progress_tracker
    participant RUN as PipelineRunner (Thread)

    OP->>UI: Types URL & configures Per-Video Runtime Settings
    UI->>UI: Dynamic Preflight Card resolves video ID & channel info
    OP->>UI: Clicks "⚡ Ingest & Process Video"
    UI->>API: POST /ingest (video_url, force_rerun, overrides, dry_run)
    API->>API: verify_csrf() & get_current_user()
    API->>RUN: Spawns background worker thread
    API-->>UI: 200 OK {"status": "started", "video_id": "..."}
    
    loop Real-Time Telemetry Streaming
        RUN->>TRK: update_progress(video_id, stage, percent, speed, eta)
        SSE->>TRK: Polls active progress records
        SSE-->>UI: event: progress data: {"stage": "DOWNLOADING", "percent": 45.2, ...}
        UI->>UI: Live progress bar & throughput telemetry update
    end
    
    RUN-->>TRK: update_progress(stage="COMPLETED", percent=100.0)
    SSE-->>UI: event: complete data: {"video_id": "...", "status": "COMPLETED"}
    UI->>UI: Show completion toast & render Review Queue action button
```

---

## 3. Flow 3: Universal Clip Recovery & Auto-Fix Remediation

Clips flagged with fatal failures or non-fatal quality warnings can be diagnosed and remediated directly through the universal recovery modal:

```mermaid
sequenceDiagram
    autonumber
    actor OP as Dashboard Operator
    participant UI as Review Queue (/queue?tab=rejected)
    participant REC as ClipRecoveryService
    participant COMP as Trimmer / VideoCompositor
    participant QG as QualityGateManager
    participant DB as Relational Database

    OP->>UI: Clicks "🛠️ Recover" on rejected clip card
    UI->>REC: diagnose_clip(clip_id)
    REC-->>UI: Return failure details, gates failed, and recommended fixes
    UI->>UI: Render modal with diagnostic pills & auto-fix buttons
    
    alt Fix Action: Duration Clamp & Remux
        OP->>UI: Clicks "Clamp Duration & Re-mux"
        UI->>REC: apply_auto_fix(clip_id, "technical_clamp")
        REC->>COMP: FFmpeg stream-copy trim to target_max (-c copy -t max)
    else Fix Action: Re-Anchor Dialogue
        OP->>UI: Clicks "Re-Anchor Dialogue from Audio"
        UI->>REC: apply_auto_fix(clip_id, "reanchor_dialogue")
        REC->>DB: Scan word-level transcript and anchor hook/payoff to audio
    else Fix Action: Safe Caption Elevation
        OP->>UI: Clicks "Re-render with Safe Caption Margin"
        UI->>REC: apply_auto_fix(clip_id, "caption_safe_rerender")
        REC->>COMP: Re-render ASS subtitles with elevated margin_v (160px)
    end
    
    REC->>QG: re_evaluate_clip(clip, db)
    QG-->>REC: New status (WAITING_APPROVAL)
    REC->>DB: Update Clip.quality_check_result & commit
    REC-->>UI: {"status": "success", "message": "Remediated successfully"}
    UI->>UI: Update card badge & move clip back to Active Queue
```

---

## 4. Flow 4: Closed-Loop Bayesian A/B Title Promotion

The Bayesian title promotion daemon optimizes headline Click-Through Rates (CTR) post-publication:

```mermaid
sequenceDiagram
    autonumber
    participant DAEMON as ABWinnerPromotionDaemon
    participant DB as Relational Database
    participant SYNC as YouTubeAnalyticsSync
    participant OPT as BayesianVariantOptimizer
    participant PUB as YouTubePublisher

    DAEMON->>DB: Query published clips with >= 2 title variations
    loop For each multi-variant clip
        DAEMON->>SYNC: Ingest latest view counts, CTR, and view velocity
        DAEMON->>OPT: evaluate_variations(variations, analytics, baseline_vel, current_vel)
        OPT->>OPT: Beta-Binomial conjugate update: alpha = 1 + clicks, beta = 19 + (impr - clicks)
        OPT->>OPT: 15,000 Monte Carlo draws to compute P(B > A)
        
        alt Confidence >= 95% (Winner Detected)
            OPT-->>DAEMON: Action = PROMOTE (winning_title, confidence)
            DAEMON->>PUB: update_video_title(youtube_id, winning_title)
            PUB->>PUB: Decrypt OAuth token & call YouTube API videos().update()
            DAEMON->>DB: Set winner variation is_active=True, update generated_title
        else Velocity Drop > 50% (Circuit Breaker)
            OPT-->>DAEMON: Action = ROLLBACK (circuit_breaker_triggered=True)
            DAEMON->>PUB: update_video_title(youtube_id, baseline_title)
            DAEMON->>DB: Reset active title to baseline & log emergency rollback
        else Statistical Inconclusive
            OPT-->>DAEMON: Action = MAINTAIN (keep current variant active)
        end
    end
```
