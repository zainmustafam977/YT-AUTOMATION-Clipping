YouTube Shorts Automation Platform

Clarification Questions & Change Requests — Response to Implementation Plan

Reference: companion review of the submitted implementation plan against “YouTube Shorts Automation – Project Handover Spec.docx” (v1.0).

Document version: 1.0   |   Status: Awaiting vendor response

This document lists items from the submitted implementation plan that require clarification, correction, or an explicit decision before development proceeds. Each item is numbered for reference in vendor responses. A blank “Vendor Response” column is provided for the implementing team to fill in and return.

Critical — Resolve Before Implementation Proceeds

ID

Item

Observation

Requested Action / Question

Vendor Response

C-01

Containerization

No Dockerfile or docker-compose is present despite Spec §13.2 and NFR-06 requiring containerized services.

Confirm containerization will be included in the delivered code, not only implied by the repo layout.

C-02

Secrets management

The plan does not specify how OAuth tokens and API keys are protected at rest, as required by Spec §14.

Specify the secrets-storage mechanism to be used (e.g. a secrets manager, encrypted DB column, OS keychain) before implementation starts.

C-03

Idempotency mechanism unproven

“Resumes exactly where it left off” is asserted (FR-17, NFR-01) without showing the underlying mechanism.

Provide the specific implementation detail (e.g. unique constraint on youtube_video_id, existence check before each write).

C-04

Quality gate missing semantic check

Only technical checks (aspect ratio, duration, audio levels) are described. The “does this clip make sense standalone” LLM pass from Spec §9.8 / §18 appears to be missing or folded into an earlier, non-equivalent step.

Confirm whether a post-render semantic quality check exists, and where in the pipeline it runs.

Moderate — Clarify Before or During Build

ID

Item

Observation

Requested Action / Question

Vendor Response

M-01

WebSub renewal

PubSubHubbub subscriptions to YouTube's hub expire on a lease. No renewal logic is mentioned; detection could silently degrade to RSS-only polling over time.

Confirm subscription renewal is handled automatically.

M-02

SQLite as default store

Workable for a single-channel MVP, but SQLite serializes concurrent writers even in WAL mode.

State the explicit criteria/trigger for migrating to PostgreSQL.

M-03

Transcription alignment accuracy

Plain Whisper/faster-whisper cross-attention timestamps drift more than a forced-alignment pass (e.g. WhisperX). This affects FR-06 (never cut mid-word) and the ±100ms caption-sync acceptance bar (Spec §15.2).

Confirm whether a forced-alignment step is included, not just native model timestamps.

M-04

No active-speaker detection

Spec §9.5 calls for active-speaker detection on multi-speaker footage. The plan only has face/body detection with a center-crop fallback.

Confirm plans to add active-speaker detection, or explicitly accept this limitation for Phase 1.

M-05

Outdated face detector listed

Haar cascades are listed as interchangeable with YuNet; Haar is markedly less reliable across lighting/angle variation.

Drop Haar cascade; standardize on a modern detector (e.g. YuNet or equivalent).

M-06

Configurable segment count

FR-05 (3–8 segments per video, configurable) is not addressed anywhere in the clip-selection description.

Confirm this is implemented, not simply omitted from the write-up.

M-07

No monitoring/alerting integration

Spec §16 calls for proactive alerting (e.g. Slack/Telegram/Sentry). The dashboard's audit log is visibility only, not alerting.

Confirm an alerting hook will be included for pipeline failures.

M-08

No CI/CD pipeline

Spec §13.4 calls for an automated, test-gated deployment process; none is described.

Confirm the build/test/deploy pipeline that will gate production releases.

M-09

Verification plan doesn't test the spec's acceptance criteria

The listed tests check that components work, but none explicitly test the three named scenarios in Spec §15.2: no duplicate publish on retry, correct resume after a forced mid-job restart, and queueing (not failing) at API quota limits.

Add explicit automated tests for these three scenarios by name.

Minor

ID

Item

Observation

Requested Action / Question

Vendor Response

N-01

Repo path contains a space

`d:/Projects/YT AUTOMATION/` can cause friction with some build tools and Docker contexts.

Rename to a space-free path (e.g. yt-automation) before build begins.

N-02

Fixed vs. dynamic caption styling

The plan uses a fixed high-contrast caption style rather than the spec's dynamic background-sampling (FR-09). This is a reasonable, arguably more robust approach in practice — but it is a literal deviation from the written requirement.

Get explicit client sign-off on the fixed-style approach rather than treating it as an implicit substitution.

Strategic Question

ID

Item

Observation

Requested Action / Question

Vendor Response

Q-01

Phase 0 vs. Phase 0+1 scope

Spec §19 deliberately phases delivery starting with a small, single-channel, manual-trigger Phase 0 MVP, specifically to validate clip-selection quality before investing in detection, orchestration, and a full dashboard. This plan appears to build all 9 services, the orchestrator, and a full operator dashboard in a single pass.

Confirm whether this plan represents Phase 0 alone, or a deliberate merge of Phase 0 and Phase 1 — and if merged, confirm that this was a conscious scope decision rather than an oversight.

Summary

4 critical items should be resolved before development starts. 9 moderate items should be clarified either before or early in the build. 2 minor items are low-risk but worth explicit sign-off. 1 strategic question about scope sequencing (Q-01) should be settled first, since the answer affects how much of the critical/moderate list applies to the immediate next milestone versus a later phase.