# YouTube Shorts Automation Platform — Clarification Questions & Discovery Document

**Prepared for:** Project Sponsor (Client) & Development Team  
**Reference Document:** `YouTube Shorts Automation - Project Handover Spec.docx`  
**Purpose:** This document consolidates all critical architectural, operational, and legal questions that require confirmation before or during the discovery phase. Addressing these questions ensures the implementation aligns with client expectations, avoids scope creep, and eliminates delivery blockers.

---

## 1. Legal Preconditions & Content Rights (Spec Section 4 [A3], Section 14, Section 24)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **1.1** | Does the client hold full copyright, or explicit written authorization/license, to repurpose and monetize content from the source YouTube channel(s)? | Automated downloading, cutting, and republishing third-party content without rights exposes the channel to YouTube Copyright Strikes, Content ID claims, and channel termination. | Must be confirmed in writing by the client before Phase 1 automated publishing is enabled. |
| **1.2** | Are there third-party materials inside the source videos (e.g., licensed music, stock b-roll, sponsored segments) that must not appear in Shorts? | Re-clipped segments might inadvertently isolate copyrighted music or sponsor shout-outs without context. | If present, we can add blacklisted timestamp intervals or keyword filtering in the clip-selection prompt. |

---

## 2. YouTube Channel Configuration & API Quotas (Spec Section 4 [A1], Section 9.9, Section 11)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **2.1** | What are the exact Channel IDs for: <br>a) The source channel to watch?<br>b) The destination channel where Shorts will be published? | Needed to configure WebSub feed subscriptions and OAuth2 authorization. | Single source channel and same destination channel for Phase 0 MVP. |
| **2.2** | Does the client already have a Google Cloud Project with the **YouTube Data API v3** enabled and an OAuth2 Consent Screen configured? | Calling `videos.insert` requires OAuth2 client credentials (`client_id`, `client_secret`, and authorized user refresh token). | If not, the development team can provide a step-by-step setup guide or configure it with client delegation. |
| **2.3** | What is the expected upload volume per week (source videos & target shorts)? | YouTube Data API default daily quota is **10,000 units/day**. Each video upload consumes **~1,600 units**, meaning a standard quota supports **~6 uploads/day**. If higher volume is needed, a quota extension must be requested early. | Default to 1–2 source videos/week generating 3–5 shorts each, staying within the standard 10k quota. |
| **2.4** | What privacy status should published Shorts receive initially? | Options: `private`, `unlisted`, or `public`. | `unlisted` during Phase 0/1 testing so output can be reviewed live on YouTube before going public. |

---

## 3. Editorial & AI Clip Selection (Spec Section 4 [A5], Section 6 [FR-04, FR-05], Section 9.4)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **3.1** | How many Short segments should be extracted per source video? | Balances content volume against API costs and publishing frequency. | **3 to 5 segments** default (configurable per channel between 1 and 8). |
| **3.2** | What is the target duration for generated Shorts? | YouTube Shorts support up to 3 minutes, but maximum audience retention occurs at **30–60 seconds**. | **30 to 58 seconds** default, with a strict ceiling under 60 seconds. |
| **3.3** | What editorial archetype best fits the channel? <br>(e.g., Quick tips / actionable advice, Story / narrative arc, Controversial opinion / debate, Humor / funny moments) | Informs the LLM prompt rubric for evaluating "hook strength" and "payoff". | We configure customizable prompt profiles (e.g., "Educational / How-To", "Podcast / Interview", "Storytelling"). |
| **3.4** | Is the source content primarily a single speaker, multi-speaker dialogue/podcast, or screen-share/tutorial? | Dictates whether active-speaker tracking or screen-crop handling is required. | Start with single-speaker optimization; activate active-speaker tracking for podcasts. |

---

## 4. Visual Styling, Captions & Branding (Spec Section 6 [FR-08, FR-09], Section 9.6)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **4.1** | Caption styling: Does the client prefer the modern **high-contrast karaoke style** (bold white font, thick black stroke, yellow/cyan active word highlight) or custom brand colors/fonts? | Standard karaoke style is proven to deliver the highest mobile legibility and watch-time retention across TikTok, Reels, and Shorts. | High-contrast bold sans-serif (e.g. Montserrat/The Bold Font/Inter) with dynamic word-by-word highlight. |
| **4.2** | Should captions dynamically switch colors based on background video luminance (FR-09), or is a universal high-contrast outline preferred? | Universal high-contrast outline + drop shadow is the industry standard (CapCut/Submagic) and avoids erratic color flickering across scene cuts. | High-contrast with background safety sampling to increase shadow opacity over bright scenes. |
| **4.3** | Are there brand assets to incorporate into the final render? <br>(e.g., channel watermark/logo overlay, intro animation, outro subscribe card)? | FFmpeg compositing can add stationary or animated overlays. | None for Phase 0 MVP; added in Phase 1 if client provides PNG assets. |

---

## 5. Audio & Sound Design (Spec Section 4 [A6], Section 6 [FR-10], Section 10.7)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **5.1** | Does the client have an existing library of licensed sound effects and background tracks, or should the vendor bundle a curated royalty-free CC0/commercial-use sound pack? | Ensures 100% compliance with YouTube copyright and Content ID monetization policies. | Bundle a curated, vetted set of 20+ royalty-free SFX (whooshes, pops, dings, dramatic hits) with documented commercial licenses. |
| **5.2** | Should background music be automatically added behind speech with audio ducking, or kept to speech + accent sound effects only? | Adding music requires genre matching; some creators prefer original source audio without background music. | Default: speech + accent SFX; background music disabled by default, toggleable per channel. |

---

## 6. Human Review & Quality Gate Governance (Spec Section 4 [A4], Section 6 [FR-14], Section 9.8)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **6.1** | How should the client or operator be notified when new Shorts are waiting for approval? <br>(e.g., Web dashboard only, Slack webhook, Discord channel, Telegram bot, or Email)? | Dictates alerting integration priorities. | Web Dashboard + Slack or Discord webhook notification with 1-click preview link. |
| **6.2** | What criteria should trigger automatic promotion from "Human Review Required" to "Fully Unattended Publishing"? | Risk management during onboarding. | Keep human approval enabled for the first 10 published Shorts; client can toggle off per channel when satisfied with quality. |

---

## 7. Infrastructure, Hosting & Compute (Spec Section 4 [A8], Section 13)

| # | Question | Impact / Rationale | Recommended Default |
|---|---|---|---|
| **7.1** | Where will the pipeline be hosted for staging and production? <br>(e.g., Client-provided Cloud account [GCP / AWS], a dedicated GPU VM, or managed container platform)? | Determines deployment automation scripts and GPU driver configuration. | Docker Compose on an on-demand cloud VM (e.g. GCP Compute Engine with T4/L4 GPU or Hetzner/RunPod for cost efficiency). |
| **7.2** | What LLM provider and API key will be used for clip selection and semantic checks? <br>(Google Gemini, OpenAI GPT-4o-mini, or Anthropic Claude)? | Direct API cost and latency impact. | **Gemini 3.8 Flash** (`LLM_MODEL_NAME = "gemini-3.8-flash"` in `shorts_engine/config/settings.py`) — the current production model. Gemini 1.5 Flash and 2.0 Flash were retired on June 1, 2026 and are no longer available. |

---

## Summary of Recommended Immediate Actions

1. **Sign-off on Phase 0 MVP Scope**: Confirm Milestone 1 focuses on the core pipeline engine and editorial quality validation on real test videos.
2. **Obtain Test YouTube Video URLs**: 1 or 2 representative long-form videos from the target channel to benchmark clip selection and transcription.
3. **Confirm LLM API Key**: Provide a Gemini or OpenAI API key to power clip selection.
4. **YouTube API Credentials**: Prepare Google Cloud OAuth2 credentials for destination channel upload.
