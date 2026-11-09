**YouTube Shorts Automation Platform**

Software Requirements & Technical Design Document

Prepared for: Implementation / Development Team

Document Version: 1.0

Status: Draft for vendor scoping and estimation

# Document Control

| **Field**         | **Value**                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------- |
| Document title    | YouTube Shorts Automation Platform — SRS & Technical Design                                       |
| Version           | 1.0                                                                                               |
| Status            | Draft — prepared to support vendor scoping, estimation, and implementation                        |
| Owner             | Project sponsor (client)                                                                          |
| Intended audience | Implementing software company / development team                                                  |
| Purpose           | Hand over a complete functional, technical, and operational specification for building the system |

# Table of Contents

1\. Executive Summary

2\. Project Background & Objectives

3\. Scope

4\. Assumptions Made to Complete This Specification

5\. Stakeholders & Roles

6\. Functional Requirements

7\. Non-Functional Requirements

8\. System Architecture Overview

9\. Detailed Module Specifications

10\. Data Model

11\. External Integrations & Third-Party Dependencies

12\. Orchestration & Job Lifecycle

13\. Infrastructure & Deployment Plan

14\. Security Requirements

15\. Testing & QA Strategy

16\. Monitoring, Logging & Alerting

17\. Risk Register

18\. Acceptance Criteria / Definition of Done

19\. Delivery Phases & Milestones

20\. Maintenance & Support Considerations

21\. Information & Access the Client Must Provide

22\. Success Metrics / KPIs

23\. Glossary

Appendix A: Suggested Repository Structure

Appendix B: Sample Data Structures

24\. Content Rights Note

# 1\. Executive Summary

This document specifies a software system that automates the conversion of a source creator's long-form YouTube videos into short-form, vertical "Shorts" content. On detecting a new upload, the system downloads the video, identifies the most compelling short segments, reframes them to a 9:16 portrait aspect ratio while keeping the speaker in frame, adds styled synchronized captions and contextual sound effects, renders the final files, and publishes them to YouTube — running unattended on an ongoing basis.

The client's original requirements were intentionally high-level ("automate my work"). This document translates that intent into a complete, implementable specification: functional and non-functional requirements, system architecture, module-by-module design, data model, integrations, security, testing, delivery phases, and the assumptions made to fill gaps the client did not specify. Any assumption below should be confirmed or corrected with the client before implementation begins.

The system is designed to run reliably over the long term with minimal manual intervention, while including a human-review option that can be enabled while trust in the automated judgment is being established.

# 2\. Project Background & Objectives

The client currently repurposes long-form YouTube videos into short-form clips manually: identifying good moments, cutting them, reframing to portrait, adding captions, and uploading. The objective of this project is to replace that manual workflow with an automated pipeline that is at least as good editorially and dramatically faster and more consistent to operate.

## 2.1 Objectives

- Automatically detect new uploads from one or more specified YouTube channels.
- Automatically identify the most compelling short-form segments from each source video.
- Automatically reframe, caption, and enhance each segment to a modern, professional short-form style.
- Automatically publish finished Shorts to YouTube, with an optional human-approval gate.
- Operate reliably, unattended, for extended periods without manual recovery from failures.
- Be extensible to additional source channels and additional output styles over time.

## 2.2 Success looks like

- A new upload results in one or more published Shorts without manual editing work.
- The system recovers from transient failures (network issues, API errors) on its own.
- Clip selection quality is good enough that most outputs require no manual rejection.
- The client can add or pause watched channels without engaging the development team.

# 3\. Scope

## 3.1 In scope (initial delivery)

- Detection of new uploads on client-specified YouTube channel(s).
- Download and transcription of source video audio.
- AI-assisted identification and ranking of candidate short segments.
- Cutting, 9:16 reframing with subject tracking, captioning, and contextual sound-effect/music mixing.
- Rendering of final, publish-ready video files.
- An automated quality check, plus an optional human-approval step before publishing.
- Automated publishing to YouTube via the official API, with generated titles/descriptions/tags.
- A persistent record of every video processed and every clip published.
- Basic operator visibility into pipeline status and failures (logs and/or a simple dashboard).

## 3.2 Out of scope (initial delivery — candidates for later phases)

- Publishing to platforms other than YouTube (e.g. TikTok, Instagram Reels) — architecture should not preclude this later.
- A full multi-tenant SaaS product with self-serve customer sign-up and billing.
- Multi-language translation/dubbing of captions.
- A polished end-user mobile app; a simple web dashboard is sufficient initially.
- Advanced analytics/feedback-loop optimization based on published-clip performance (proposed for a later phase).

_Anything in 3.2 can be brought into scope in a later phase; the architecture in Section 8 is deliberately modular so these can be added without a redesign._

# 4\. Assumptions Made to Complete This Specification

The client's original brief did not specify the following details. Reasonable, professional defaults have been assumed so the vendor can scope and estimate the work. Each should be explicitly confirmed with the client before or during discovery.

| **#** | **Assumption**                                                                                                                    | **Rationale**                                                                                      |
| ----- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| A1    | Initial deployment targets a single source channel, with the architecture supporting multiple channels from Phase 2 onward.       | Reduces initial delivery risk; multi-channel is a scaling concern, not a redesign.                 |
| A2    | Source and output content is primarily in one spoken language initially.                                                          | Avoids scoping multi-language transcription/translation into the MVP.                              |
| A3    | The client holds, or will confirm, the rights to repurpose the source channel's content.                                          | This is a legal precondition independent of the technical build; see Section 14.                   |
| A4    | A human-approval step is enabled by default until the client is confident in automated quality, then can be disabled per channel. | Balances automation goals against the risk of publishing a poor clip unattended.                   |
| A5    | 3–8 short segments are generated per source video by default, configurable.                                                       | Matches typical short-form repurposing volume; needs client confirmation.                          |
| A6    | Sound effects and music are drawn from a properly licensed royalty-free library provided or approved by the client.               | Avoids introducing licensing risk into the automated pipeline.                                     |
| A7    | The target output format is YouTube Shorts (≤ 3 minutes, 9:16).                                                                   | Matches the client's stated requirement; other platforms are out of scope initially.               |
| A8    | Compute runs in the cloud (on-demand GPU) rather than on client-owned hardware.                                                   | Matches typical availability for a small/solo client; can be revisited if the client has hardware. |

# 5\. Stakeholders & Roles

| **Role**                                 | **Responsibility**                                                                                                                         |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Client / project sponsor                 | Owns the source and destination YouTube channels, confirms content rights, approves scope, provides brand/style preferences, reviews UAT.  |
| Implementing software company            | Designs in detail, builds, tests, deploys, and hands over the system per this specification; raises clarifying questions during discovery. |
| Project / delivery manager (vendor side) | Owns timeline, phase delivery, and communication with the client.                                                                          |
| Backend / pipeline engineer(s)           | Builds the detection, ingestion, orchestration, and publishing services.                                                                   |
| ML / media engineer(s)                   | Builds transcription, clip-selection AI, tracking/reframing, and rendering components.                                                     |
| QA engineer                              | Owns the test plan in Section 15 and sign-off before each phase release.                                                                   |
| DevOps / infrastructure engineer         | Owns containerization, deployment, monitoring, and cost management.                                                                        |

# 6\. Functional Requirements

Each requirement is tagged Must (required for the system to be usable), Should (important, not blocking), or Could (nice to have / later phase).

| **ID** | **Requirement**                                                                                             | **Priority**    |
| ------ | ----------------------------------------------------------------------------------------------------------- | --------------- |
| FR-01  | Monitor one or more specified YouTube channels and detect new uploads within minutes of publication.        | Must            |
| FR-02  | Download the full source video and audio for each detected upload.                                          | Must            |
| FR-03  | Generate a word-level, timestamped transcript of the source video's spoken audio.                           | Must            |
| FR-04  | Automatically identify and rank candidate short segments by hook strength, coherence, and standalone value. | Must            |
| FR-05  | Support a configurable number of segments extracted per source video (default 3–8).                         | Should          |
| FR-06  | Cut selected segments without splitting words or natural breathing points.                                  | Must            |
| FR-07  | Reframe each segment to 9:16 portrait, tracking and centering the primary speaker/subject.                  | Must            |
| FR-08  | Generate styled, word-synchronized captions for each segment.                                               | Must            |
| FR-09  | Automatically adjust caption color/contrast for legibility against the background.                          | Should          |
| FR-10  | Identify and mix contextually appropriate sound effects and/or music into each segment.                     | Should          |
| FR-11  | Render each segment into a final publish-ready video file.                                                  | Must            |
| FR-12  | Generate a title, description, and tags/hashtags for each segment.                                          | Must            |
| FR-13  | Run an automated quality check on each rendered segment before publishing.                                  | Must            |
| FR-14  | Support an optional human-approval step, togglable per channel or globally.                                 | Must            |
| FR-15  | Publish approved segments to YouTube as Shorts via the official API.                                        | Must            |
| FR-16  | Maintain a complete history of every video processed, segment generated, and publish action.                | Must            |
| FR-17  | Prevent duplicate processing of the same source video or duplicate publishing of the same segment.          | Must            |
| FR-18  | Provide operator visibility (logs and/or dashboard) into pipeline status and failures.                      | Should          |
| FR-19  | Allow adding, removing, or pausing watched channels without a redeployment.                                 | Should          |
| FR-20  | Support multiple source channels processed concurrently.                                                    | Could (Phase 2) |

# 7\. Non-Functional Requirements

| **ID** | **Category**         | **Requirement**                                                                                                                                   |
| ------ | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| NFR-01 | Reliability          | The pipeline resumes correctly after a crash or restart with no data loss and no duplicate publishing (full idempotency).                         |
| NFR-02 | Availability         | The detection service maintains high uptime; missed detection events delay the entire downstream pipeline.                                        |
| NFR-03 | Scalability          | The architecture supports growth from one to many source channels without a fundamental redesign.                                                 |
| NFR-04 | Performance          | End-to-end processing time per source video is predictable and reported, with targets agreed during discovery based on typical video length.      |
| NFR-05 | Security             | Credentials, tokens, and API keys are encrypted at rest and never stored in code, logs, or plaintext config committed to version control.         |
| NFR-06 | Maintainability      | Services are modular, containerized, documented, and independently deployable, with clear logs for post-incident debugging months after delivery. |
| NFR-07 | Observability        | Every pipeline stage logs sufficient detail to diagnose a failure without needing to reproduce it.                                                |
| NFR-08 | Cost efficiency      | Compute resources, especially GPU, scale down when idle rather than running continuously.                                                         |
| NFR-09 | Compliance           | The system adheres to YouTube's Terms of Service and API usage policies at all times.                                                             |
| NFR-10 | Usability (operator) | Pipeline status and failure alerts are interpretable by a non-developer operator with basic technical familiarity.                                |

# 8\. System Architecture Overview

The system is built as a set of small, independently deployable services connected through a job queue and a persistent state database — not a single monolithic script. Each service can fail and retry without corrupting the rest of the pipeline or duplicating work, which is what allows the system to run unattended for long periods.

## 8.1 End-to-end data flow

- 1\. Detection — a new upload on a watched channel is detected and queued as a job.
- 2\. Ingestion — the source video and audio are downloaded.
- 3\. Transcription — a word-level timestamped transcript is produced.
- 4\. Clip selection — candidate segments are identified, scored, and ranked.
- 5\. Per selected segment, in parallel:
- 5a. Reframing — the segment is cropped to 9:16, tracking the speaker/subject.
- 5b. Captioning & SFX — styled captions and contextual sound cues are generated.
- 5c. Rendering — picture, captions, and audio are composited into a final file.
- 6\. Quality gate — an automated check (and optionally a human reviewer) approves or rejects the clip.
- 7\. Publishing — approved clips are uploaded to YouTube with generated metadata.

## 8.2 Design principles

- Idempotency — every step can safely be retried without re-doing completed work or duplicating output.
- Separation of deterministic and AI-judgment work — CV/media processing runs as fast, deterministic code; an AI model is invoked only for genuine judgment calls.
- Explicit state — every job's progress is persisted, so a crash or redeploy never loses track of in-flight work.
- Fail loud, not silent — failures raise an alert and land in a reviewable queue rather than being silently dropped or retried forever.

# 9\. Detailed Module Specifications

Each module below is designed to be built, tested, and deployed independently.

### 9.1 Detection Service

Watches configured YouTube channels and raises an event the moment a new video is published.

Inputs: List of channel IDs to watch.

Outputs: A "new_video" job with the video ID and channel ID.

Core logic: Subscribe to each channel's PubSubHubbub/WebSub feed for near-instant, quota-free push notification. Run a scheduled RSS poll (every 5–10 minutes) as a fallback so a missed webhook is still caught.

Suggested technology: YouTube WebSub feed subscription + a scheduled RSS poller.

Failure handling: Store the last-seen video ID per channel so a restart never reprocesses or misses an upload; alert if no event has been received for an unusually long period on an active channel.

### 9.2 Ingestion Service

Downloads the source video and audio for a detected upload.

Inputs: Video ID.

Outputs: Local or object-storage video/audio file, plus source metadata (title, duration, existing captions if any).

Core logic: Use yt-dlp to fetch the best-quality video and audio, plus any existing platform captions as a transcription cross-check.

Suggested technology: yt-dlp (pinned version, scheduled updates).

Failure handling: Retry with exponential backoff on transient failures; alert (rather than silently degrade) if yt-dlp itself starts failing broadly, since this usually indicates an upstream YouTube change requiring a version update.

### 9.3 Transcription Service

Produces a precise, timestamped transcript used by every downstream stage.

Inputs: Audio track.

Outputs: Word-level timestamped transcript with speaker diarization.

Core logic: Run automatic speech recognition with word-level alignment; diarize when multiple speakers are present.

Suggested technology: WhisperX or faster-whisper, GPU-accelerated.

Failure handling: Queue and retry if the GPU worker is unavailable rather than silently falling back to a lower-quality path; log which processing path was used for each job.

### 9.4 Clip Selection Service (AI)

Identifies which segments of the source video are worth publishing as standalone Shorts.

Inputs: Transcript (and, where available, the raw video for multimodal analysis).

Outputs: A ranked list of candidate segments, each with a start time, end time, score, and reason.

Core logic: A generator pass proposes candidate segments; a separate critic pass re-scores each candidate against a fixed rubric (hook strength, standalone coherence, payoff, length fit). Output is constrained to a fixed JSON schema, never free text. Where visual-only highlights matter, a multimodal video-understanding pass runs alongside the transcript-based pass and results are merged.

Suggested technology: An LLM API for the generator/critic passes; optionally a video-understanding API for multimodal analysis.

Failure handling: Retry once on schema-validation failure; if it still fails, flag the video for manual review instead of guessing at a malformed response.

### 9.5 Reframing / Tracking Service

Converts a landscape segment to portrait while keeping the speaker or subject centered in frame.

Inputs: Source video and the selected segment's start/end timestamps.

Outputs: A 9:16 cropped video for the segment.

Core logic: Detect faces/people per frame; for multi-speaker footage, detect the active speaker; compute a smoothed crop path (to avoid jittery panning) and apply it as a crop filter.

Suggested technology: A person/face detection model, active-speaker detection, smoothing (e.g. a moving average or Kalman filter), FFmpeg for the crop.

Failure handling: Fall back to a center-weighted crop when no face is detected for a stretch (e.g. b-roll) rather than erroring the job.

### 9.6 Captioning & Sound Effects Service

Adds styled, synchronized captions and contextual audio elements to a segment.

Inputs: Segment transcript (word-level) and the reframed video.

Outputs: A caption file and a set of selected sound-effect/music cues.

Core logic: Build word-synchronized ("karaoke-style") captions; sample the background under the caption region to choose readable text/outline styling; tag the segment's content for tone (joke, reveal, stat, question) and map tags to a fixed, licensed sound-effect/music library.

Suggested technology: A subtitle-generation library producing a rich subtitle format, FFmpeg/subtitle-rendering for burn-in, an enum-constrained tagging step for sound selection.

Failure handling: Default to no sound effect when tagging confidence is low, rather than guessing at a file that may not exist.

### 9.7 Rendering Service

Produces the final, publish-ready video file.

Inputs: Reframed video, caption file, audio layers (voice, sound effects, background music).

Outputs: Final composited video file.

Core logic: A single rendering pass composites the video, burns in captions, and mixes the audio layers (with background music ducked under speech).

Suggested technology: FFmpeg.

Failure handling: Validate the output file (duration, resolution, non-zero size, playable) before marking the job complete, so a corrupt render is caught rather than published.

### 9.8 Quality Gate

A final checkpoint before a clip is allowed to publish.

Inputs: The final rendered clip.

Outputs: A pass/fail decision with a reason.

Core logic: An automated check evaluates whether the clip stands on its own. Early in the project's life this step routes to a human-approval queue instead of auto-publishing; it can be switched to fully automatic once the operator trusts the pipeline's judgment.

Suggested technology: An LLM-based verifier pass; an operator-facing approval queue (dashboard, or a lightweight review channel such as email/Slack).

Failure handling: A rejected clip is retained with its reason and does not consume another attempt automatically — it is surfaced for review.

### 9.9 Publishing Service

Publishes an approved clip to YouTube.

Inputs: Approved clip and its generated metadata.

Outputs: A published YouTube Shorts video and a record of the publish event.

Core logic: Authenticate via OAuth2 and call the YouTube Data API to upload the video with its title, description, and tags; enforce character-limit truncation on generated metadata in code before submission.

Suggested technology: YouTube Data API v3 (videos.insert), OAuth2 client library.

Failure handling: On quota exhaustion, queue the job for the next available window rather than dropping it; log every publish with the resulting video ID for auditing.

# 10\. Data Model

A relational database (e.g. PostgreSQL) persists all pipeline state. Suggested schema below; exact types may be adapted to the chosen database engine.

### 10.1 channels

| **Field**                 | **Type**  | **Description**                                               |
| ------------------------- | --------- | ------------------------------------------------------------- |
| id                        | UUID (PK) | Internal identifier.                                          |
| youtube_channel_id        | varchar   | YouTube's channel ID.                                         |
| name                      | varchar   | Display name.                                                 |
| watch_enabled             | boolean   | Whether detection is currently active for this channel.       |
| last_seen_video_id        | varchar   | Used to avoid reprocessing on restart.                        |
| publish_target_channel_id | varchar   | Destination channel for publishing, if different from source. |
| human_review_required     | boolean   | Per-channel toggle for the approval gate.                     |

### 10.2 source_videos

| **Field**        | **Type**  | **Description**                                                                        |
| ---------------- | --------- | -------------------------------------------------------------------------------------- |
| id               | UUID (PK) | Internal identifier.                                                                   |
| channel_id       | UUID (FK) | References channels.id.                                                                |
| youtube_video_id | varchar   | Source video ID on YouTube.                                                            |
| title            | varchar   | Source video title.                                                                    |
| duration_sec     | integer   | Length of the source video.                                                            |
| status           | enum      | DETECTED / DOWNLOADING / TRANSCRIBING / ANALYZING / SEGMENTS_SELECTED / DONE / FAILED. |
| downloaded_path  | varchar   | Storage location of the source file.                                                   |
| created_at       | timestamp | When the job was created.                                                              |

### 10.3 transcripts

| **Field**       | **Type**  | **Description**                                        |
| --------------- | --------- | ------------------------------------------------------ |
| id              | UUID (PK) | Internal identifier.                                   |
| source_video_id | UUID (FK) | References source_videos.id.                           |
| word_level_json | jsonb     | Word-level timestamped transcript with speaker labels. |
| language        | varchar   | Detected/assumed spoken language.                      |

### 10.4 clip_candidates

| **Field**         | **Type**  | **Description**                                |
| ----------------- | --------- | ---------------------------------------------- |
| id                | UUID (PK) | Internal identifier.                           |
| source_video_id   | UUID (FK) | References source_videos.id.                   |
| start_ms / end_ms | integer   | Segment boundaries in the source video.        |
| score             | float     | Ranking score from the clip-selection service. |
| reason            | text      | Model-generated justification, for auditing.   |
| selected          | boolean   | Whether this candidate proceeded to rendering. |

### 10.5 clips

| **Field**            | **Type**  | **Description**                                                                    |
| -------------------- | --------- | ---------------------------------------------------------------------------------- |
| id                   | UUID (PK) | Internal identifier.                                                               |
| clip_candidate_id    | UUID (FK) | References clip_candidates.id.                                                     |
| rendered_path        | varchar   | Storage location of the final rendered file.                                       |
| caption_style        | jsonb     | Styling parameters used.                                                           |
| sfx_tags_json        | jsonb     | Sound-effect/music cues applied.                                                   |
| status               | enum      | RENDERING / QUALITY_CHECK / APPROVED / REJECTED / PUBLISHING / PUBLISHED / FAILED. |
| quality_check_result | text      | Automated verifier's pass/fail reason.                                             |

### 10.6 publish_log

| **Field**                       | **Type**            | **Description**                                |
| ------------------------------- | ------------------- | ---------------------------------------------- |
| id                              | UUID (PK)           | Internal identifier.                           |
| clip_id                         | UUID (FK)           | References clips.id.                           |
| youtube_video_id                | varchar             | Published video's ID.                          |
| published_at                    | timestamp           | Publish time.                                  |
| title / description / tags_json | text / text / jsonb | Metadata submitted at publish time, for audit. |

### 10.7 sfx_library

| **Field**    | **Type**  | **Description**                                |
| ------------ | --------- | ---------------------------------------------- |
| id           | UUID (PK) | Internal identifier.                           |
| tag          | varchar   | Enum-style tag (e.g. whoosh, ding, sting).     |
| file_path    | varchar   | Location of the licensed asset.                |
| license_note | text      | License terms/source, for compliance auditing. |

# 11\. External Integrations & Third-Party Dependencies

| **Integration**                                        | **Purpose**                                                    | **Notes**                                                                                                                              |
| ------------------------------------------------------ | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| YouTube WebSub + RSS                                   | Detect new uploads                                             | Public feed; no special credentials required for the feed itself.                                                                      |
| yt-dlp                                                 | Download source video/audio                                    | Open-source and unofficial; requires periodic version updates as YouTube changes.                                                      |
| Speech-to-text engine (e.g. WhisperX / faster-whisper) | Word-level transcription                                       | GPU-accelerated; can be self-hosted or a hosted equivalent.                                                                            |
| LLM API                                                | Clip selection, captions emphasis, titles/descriptions         | Usage-based cost; outputs must be schema-constrained, never trusted as free text.                                                      |
| Video-understanding API (optional, e.g. TwelveLabs)    | Multimodal highlight detection beyond transcript-only analysis | Adds capability for visually-driven highlights (reactions, on-screen graphics).                                                        |
| Licensed SFX/music library                             | Contextual sound design                                        | Must be explicitly licensed for redistribution in published videos; track per-asset licensing.                                         |
| YouTube Data API v3                                    | Publishing                                                     | Requires a Google Cloud project, OAuth2 consent, and quota management (default 10,000 units/day; an upload costs roughly 1,600 units). |
| FFmpeg                                                 | Cutting, cropping, compositing, rendering                      | Open source; confirm the specific build's license terms are compatible with commercial use.                                            |
| Person/face detection model                            | Subject tracking for reframing                                 | Open-source model; confirm license terms for commercial use.                                                                           |

# 12\. Orchestration & Job Lifecycle

Every source video moves through an explicit, persisted state so a crash or restart never loses track of in-flight work:

- DETECTED → DOWNLOADING → TRANSCRIBING → ANALYZING → SEGMENTS_SELECTED
- Per segment: REFRAMING → CAPTIONING → RENDERING → QUALITY_CHECK → APPROVED → PUBLISHING → PUBLISHED
- Any stage may transition to FAILED with a reason and a retry count; repeated failures land the job in a dead-letter queue for manual inspection rather than retrying indefinitely.

## 12.1 Orchestrator options

| **Option**                                | **Pros**                                                                              | **Cons**                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| n8n (visual workflow tool, self-hostable) | Fast to stand up; built-in retry/branching; approachable for non-engineers to inspect | Less flexible for highly custom logic than raw code       |
| Python + Celery + Redis                   | Full code control; easy to unit test; fits a code-first engineering team              | More setup work; retry/branching logic must be hand-built |

Recommendation: n8n for the fastest path to a reliably running system; Celery + Redis if the implementing team prefers everything to live in version-controlled code.

## 12.2 Idempotency & retry policy

- Every step checks whether its output already exists before repeating work (e.g. "is this segment already rendered").
- Failures retry with capped exponential backoff (e.g. up to 5 attempts).
- A job that exhausts its retries is not silently dropped — it is flagged and alerted.

# 13\. Infrastructure & Deployment Plan

## 13.1 Environments

- Development — local/staging with a test or unlisted YouTube upload target, so a broken render can never accidentally publish publicly.
- Production — the live pipeline, publishing to the client's real channel(s).

## 13.2 Compute

- Each service is containerized (Docker) and composed together; Kubernetes is not needed until running many channels at real scale — avoid over-engineering the initial delivery.
- Transcription, tracking, and rendering are GPU-bound. An on-demand/rented GPU instance (spun up per job) is recommended over an always-on GPU box for cost efficiency at low-to-moderate volume.
- CPU-only services (detection, orchestration, publishing) can run on a modest always-on instance.

## 13.3 Storage

- Local/fast disk for actively-processing jobs.
- Object storage (e.g. an S3-compatible bucket) for source videos and final renders once complete.
- A lifecycle policy deletes intermediate files after a defined retention period to control storage cost.

## 13.4 CI/CD

- Each service has its own build/test pipeline; changes deploy independently.
- Automated integration tests (Section 15) run before any production deployment.
- Rollback plan: each service version is tagged so a bad deployment can be reverted quickly.

# 14\. Security Requirements

- All credentials (YouTube OAuth refresh tokens, LLM/API keys, database credentials) are stored in a secrets manager — never in code, plaintext config, or version control.
- The YouTube API integration uses a dedicated, least-privilege OAuth application scoped only to the required upload and channel-read permissions.
- Credentials are rotated periodically; every publish action is logged with enough detail for audit.
- Network access between internal services is restricted to what each service actually needs.
- Any operator-facing dashboard requires authentication; it is not exposed publicly without access control.
- Source videos and generated content are handled per the client's data-retention preferences (see also Section 4, assumption A3 on content rights).

# 15\. Testing & QA Strategy

## 15.1 Test levels

| **Level**                     | **Scope**                                       | **Example**                                                                                  |
| ----------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Unit tests                    | Individual functions/services in isolation      | Word-timestamp cut-point selection never lands mid-word.                                     |
| Integration tests             | Two or more services working together           | Detection → ingestion → transcription runs end-to-end on a known test video.                 |
| End-to-end tests              | The full pipeline, source video to publish      | A staged test video produces an approved, correctly formatted Short in the test environment. |
| User acceptance testing (UAT) | Client reviews real output against expectations | Client reviews a batch of generated Shorts before automatic publishing is enabled.           |

## 15.2 Specific acceptance checks

- Cuts never split a word or land mid-breath (spot-checked against source audio).
- Reframed video keeps the speaker in frame across a range of test videos (single speaker, multi-speaker, screen-share/no-face segments).
- Captions are synchronized to within an acceptable tolerance (e.g. ±100ms) of the spoken word.
- Caption text remains legible against varied backgrounds (light, dark, busy footage).
- No duplicate publish occurs when a job is deliberately retried.
- The pipeline resumes correctly after a forced mid-job restart.
- Publishing respects YouTube API quota and queues rather than fails when the daily limit is reached.

# 16\. Monitoring, Logging & Alerting

- Structured, per-job logging at every pipeline stage (not bare print statements), so a failure is traceable without reproduction.
- An alert (e.g. Slack/Telegram/email webhook, or a tool such as Sentry) fires the moment a stage fails, rather than the failure being discovered days later.
- Health checks on every long-running worker so a hung process is automatically restarted.
- A simple status view (dashboard or regularly-refreshed report) showing: videos detected, in-progress jobs, clips awaiting approval, and recent failures.

# 17\. Risk Register

| **Risk**                                                                                   | **Likelihood** | **Impact** | **Mitigation**                                                                                              |
| ------------------------------------------------------------------------------------------ | -------------- | ---------- | ----------------------------------------------------------------------------------------------------------- |
| YouTube changes break yt-dlp downloading                                                   | Medium         | High       | Pin versions, monitor upstream releases, budget ongoing maintenance time.                                   |
| YouTube API quota limits publishing throughput                                             | Medium         | Medium     | Request a quota increase; queue overflow rather than drop jobs.                                             |
| Source content copyright/rights issue                                                      | Low–Medium     | High       | Confirm rights before enabling automated publishing; add a manual legal review gate if uncertain.           |
| AI-selected clips are low quality or nonsensical                                           | Medium         | Medium     | Human-review gate in early phases; automated quality-gate pass; refine rubric with real feedback over time. |
| GPU compute costs scale unpredictably with volume                                          | Medium         | Medium     | Use on-demand/rented GPU rather than always-on; monitor usage against budget.                               |
| Tracking fails on unusual video styles (multi-speaker, no visible face, screen recordings) | Medium         | Low–Medium | Fall back to center-crop; flag low-confidence tracking for review.                                          |
| Sound effect/music licensing issue                                                         | Low            | High       | Use a properly licensed library only; track per-asset license.                                              |
| Third-party API/library changes break the pipeline unexpectedly                            | Medium         | Medium     | Automated integration tests, staged rollout, alerting on failure-rate spikes.                               |

# 18\. Acceptance Criteria / Definition of Done

A delivered phase is considered complete when:

- All "Must" functional requirements for that phase (Section 6) pass their acceptance checks (Section 15.2).
- The pipeline has run unattended across multiple real source videos without manual recovery from a failure.
- The client has reviewed and approved a representative sample of generated Shorts.
- Monitoring and alerting are live and verified (a deliberately triggered failure produces an alert).
- Documentation for operating and extending the system (adding a channel, adjusting styling, reading logs) is handed over.

# 19\. Delivery Phases & Milestones

Relative sizing (S/M/L) is indicative only, to support scoping conversations — the implementing team should provide firm estimates based on their own velocity.

| **Phase**                   | **Deliverable**                                                                                                                                                                                         | **Relative size** |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| Phase 0 — MVP               | Manually triggered pipeline for one channel: download → transcribe → AI clip selection → basic center-crop reframe → simple captions → render → manual review → manual upload. Proves the core concept. | M                 |
| Phase 1 — Automate the loop | Detection service, orchestrator with retries/state DB, tracking-based reframe, karaoke captions, contextual SFX, quality gate, auto-publish for approved clips.                                         | L                 |
| Phase 2 — Scale             | Multi-channel support, GPU autoscaling, expanded SFX/music library, operator dashboard.                                                                                                                 | M                 |
| Phase 3 — Optimization      | Title A/B testing, rubric refinement using real published-clip performance data, per-channel style presets.                                                                                             | M                 |

# 20\. Maintenance & Support Considerations

- yt-dlp and any other unofficial YouTube-facing tooling requires periodic updates as YouTube changes its site — budget ongoing maintenance time, not just initial build time.
- Model/API costs (LLM calls, GPU time) should be monitored monthly against actual usage.
- The clip-selection rubric benefits from periodic review against which published clips actually performed well (Phase 3 feedback loop).
- A documented runbook should cover: how to add/pause a channel, how to read a failed-job alert, how to manually re-trigger a stuck job, and who to contact for API/credential issues.

# 21\. Information & Access the Client Must Provide

- The source YouTube channel(s) to watch, with confirmation of the right to repurpose that content.
- The destination YouTube channel and credentials where Shorts will be published.
- A Google Cloud project with the YouTube Data API enabled and OAuth consent configured (or authorization for the vendor to set this up on the client's behalf).
- Any brand preferences: caption fonts/colors/style, intro/outro elements, tone.
- Licensed sound effect/music assets, or a decision to have the vendor source a licensed library.
- A decision on the human-review requirement: fully automatic vs. approval-gated publishing, and for how long.
- Expected volume: number of source videos per week and desired clips per video, to size compute and API quota needs.

# 22\. Success Metrics / KPIs

- Time from source video upload to published Short(s).
- Percentage of AI-selected clips approved without manual rejection.
- Pipeline uptime — successful automatic runs vs. runs requiring manual intervention.
- YouTube API quota utilization against the daily limit.
- (Post-launch, Phase 3) audience engagement on published Shorts, if performance data is fed back into the selection rubric.

# 23\. Glossary

| **Term**              | **Definition**                                                                                                 |
| --------------------- | -------------------------------------------------------------------------------------------------------------- |
| Shorts                | YouTube's short-form vertical video format, currently up to 3 minutes.                                         |
| ASR                   | Automatic Speech Recognition — converting spoken audio to text.                                                |
| Diarization           | Identifying which speaker said which part of a transcript.                                                     |
| WebSub / PubSubHubbub | A push-notification protocol YouTube supports for near-instant new-upload alerts.                              |
| Idempotency           | A property where repeating an operation produces the same result as doing it once — critical for safe retries. |
| Quality gate          | A checkpoint (automated and/or human) that a clip must pass before publishing.                                 |
| Dead-letter queue     | Where jobs land after exhausting their retry attempts, for manual inspection.                                  |
| 9:16                  | Portrait aspect ratio used by Shorts, Reels, and TikTok.                                                       |

# Appendix A: Suggested Repository Structure

```
shorts-pipeline/
  services/
    detection/
    ingestion/
    transcription/
    clip_selection/
    reframing/
    captioning/
    rendering/
    quality_gate/
    publishing/
  orchestration/      (workflow definitions)
  db/                 (schema + migrations)
  assets/sfx_library/
  docker-compose.yml
  config/
    dev.env
    prod.env
```

# Appendix B: Sample Data Structures

### Clip candidate (output of the clip-selection service)

```
{
  "start_ms": 128400,
  "end_ms": 154200,
  "score": 0.87,
  "reason": "Strong hook in first 2s, self-contained punchline, clean end point"
}
```

### Job status (operator-facing)

```
{
  "source_video_id": "a1b2c3",
  "status": "SEGMENTS_SELECTED",
  "segments_total": 5,
  "segments_published": 2,
  "segments_awaiting_review": 3,
  "last_error": null
}
```

# 24\. Content Rights Note

Before automated publishing is enabled, the client must confirm they hold the rights, or have explicit permission, to repurpose the source channel's content. Auto-clipping and re-uploading another party's video without that permission carries real copyright and platform-policy risk on YouTube, independent of how well the technical pipeline works. This is a legal precondition, not a technical one, and should be confirmed before Phase 1 (automatic publishing) goes live.