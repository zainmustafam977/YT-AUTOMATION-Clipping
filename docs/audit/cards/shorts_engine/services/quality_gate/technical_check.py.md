# Assessment Cards: shorts_engine/services/quality_gate/technical_check.py

---

## Unit 1: TechnicalVerifier.verify
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/technical_check.py:16-91` |
| What it does | Validates rendered video containers using `ffprobe`: verifies 1080x1920 resolution, checks duration against configured bounds with container muxing tolerance (-1.5s / +2.0s), validates audio stream presence, and checks minimum file size (> 50 KB). |
| Industrial-grade? | **B+** - Precise technical compliance inspection, but uses un-supervised `subprocess.run` without timeout protection. |
| Checklist | **Error handling:** Pass (gracefully catches non-zero exit codes, corrupt JSON, missing files). **Timeouts:** Fail (no timeout parameter on `subprocess.run` line 38). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (verifies file existence and non-zero size before execution). **Resource cleanup:** Pass. **Concurrency safety:** Pass (pure functional verification). **Atomicity:** Pass. **External-dependency:** Pass (`ffprobe`). **Security:** Pass (arg list prevents shell injection). **Performance:** Pass (fast metadata probe). **Observability:** Pass (returns comprehensive check dictionary). **Config:** Pass (reads settings single source of truth with runtime parameter overrides). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `ffprobe` hangs while parsing an incomplete or damaged MP4 atom header, the calling worker thread blocks indefinitely without a timeout. |
| Improvement | Add `timeout=15.0` to `subprocess.run(["ffprobe", ...])`. |
| Better approach? | Option 1: `subprocess.run(cmd, ..., timeout=15.0)` (Recommended). Option 2: Unbounded `subprocess.run` (Current - hang risk). |
| Priority / Effort | P1 / S |
