# YouTube Shorts Automation — Final Resolutions & Kickoff Confirmation

**Document Reference:** `YouTube Shorts Automation - Round 2 Outstanding Items2.docx` (v1.1)  
**Date:** September 10, 2026  
**Status:** All 7 items closed. Minor follow-ups (F-01 through F-04) confirmed. Ready for Milestone 1 execution.

---

## Addendum: Follow-up Confirmations (F-01 to F-04)

| ID | Item | Requested Action | Confirmed Implementation |
|---|---|---|---|
| **F-01** | Repo root still contains a space | Confirm outer folder handling vs package containment | The Python source code is contained inside `shorts_engine/`. All subprocess invocations use argument lists (`['ffmpeg', '-i', str(path)]`) avoiding shell word-splitting, and `pathlib.Path` is used throughout. In Docker, `COPY shorts_engine/ /app/shorts_engine/` provides complete space isolation. If the client wishes to rename the root folder on disk to `yt-automation`, the code supports it seamlessly. |
| **F-02** | Key derivation details & rotation | Specify salt, iteration count, and rotation runbook | Standardized on **PBKDF2-HMAC-SHA256 with 600,000 iterations** (OWASP standard). A 16-byte cryptographically random salt is prepended to the ciphertext along with the 12-byte GCM nonce: `[16-byte salt][12-byte nonce][ciphertext + 16-byte auth tag]`. A `rotate_secret(encrypted_b64, old_master_key, new_master_key)` utility is provided in `db/security.py` to decrypt under the old key and re-encrypt under the new key. |
| **F-03** | Segment-count validation | Add `le=8` upper-bound validation | Added strict Pydantic field validation: `MAX_SEGMENTS_PER_VIDEO: int = Field(default=5, ge=1, le=8)` and `MIN_SEGMENTS_PER_VIDEO: int = Field(default=3, ge=1, le=8)` with a model validator ensuring `MIN <= MAX`. |
| **F-04** | CD triggers before target environments exist | Gate off CD during Milestones 1–2 | Added environment condition: `if: vars.CD_ENABLED == 'true'` (defaults to `false` in repository variables during Milestones 1 and 2). CI linting and pytest tests run on every push; CD stages cleanly skip until deployment infrastructure is provisioned in Milestone 3. |

---

## Delivery Kickoff: Milestone 1 (Phase 0 MVP) Scope
Execution is commencing on **Milestone 1**:
1. `shorts_engine/` package initialization & unified settings (`config/settings.py`).
2. Security & encryption module (`db/security.py`, AES-256-GCM + PBKDF2).
3. Core database models & sessionmaker (`db/models.py`, `db/session.py`).
4. Ingestion service (`services/ingestion/downloader.py` with `yt-dlp`).
5. Transcription & forced alignment (`services/transcription/`).
6. AI clip selection (`services/clip_selection/` with Gemini 3.8 Flash & boundary snapping).
7. 9:16 portrait tracking (`services/reframing/` with YuNet ONNX).
8. Karaoke captions & SFX (`services/captioning/` with background contrast sampler).
9. FFmpeg compositing (`services/rendering/` with EBU R128 loudness normalization).
10. Two-tier Quality Gate & Post-Render Multimodal Inspector (`services/quality_gate/`).
11. Test suite (`tests/`) and CLI runner (`cli.py process --video-id <ID> --dry-run`).
