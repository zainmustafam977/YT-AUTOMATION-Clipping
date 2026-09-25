# Assessment Cards: shorts_engine/services/reframing/cropper.py

---

## Unit 1: CropPlan Data Class & to_ffmpeg_filter
| Field | Content |
|---|---|
| Location | `shorts_engine/services/reframing/cropper.py:18-44` |
| What it does | Data structure defining 9:16 crop window coordinates and rendering mode, generating FFmpeg filter strings with studio-grade Lanczos resampling (`flags=lanczos+accurate_rnd`) to 1080x1920. |
| Industrial-grade? | **A** - Enforces studio-quality Lanczos scaling with accurate rounding, avoiding blurry bilinear interpolation. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (`__repr__`). **Config:** Pass (`settings.VIDEO_SCALE_ALGORITHM`, `TARGET_WIDTH`, `TARGET_HEIGHT`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Lanczos resampling with accurate rounding (Recommended for crisp high-DPI phone screens). |
| Priority / Effort | P3 / S |

---

## Unit 2: VideoReframer.compute_crop_plan & _fallback_center_crop
| Field | Content |
|---|---|
| Location | `shorts_engine/services/reframing/cropper.py:52-182` |
| What it does | Samples video frames at 2.0 FPS across the clip window, runs YuNet neural face detection and active speaker tracking, groups faces into spatial clusters (>18% separation), centers dual-speakers if both fit within 75% of crop width, enforces safe-zone margins (20%-80%), aligns all coordinates to even pixel boundaries for YUV420p compliance, and falls back to geometric center crop if no faces are detected. |
| Industrial-grade? | **A** - Textbook video engineering: even pixel alignment prevents codec macroblock errors, multi-speaker clustering accommodates podcast panels, and safe-zone margin bounds prevent cutting off heads. |
| Checklist | **Error handling:** Pass (catches unopenable video, falls back to center crop). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (validates file existence and dimensions). **Resource cleanup:** Pass (explicit `cap.release()`). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`cv2.VideoCapture`). **Security:** Pass. **Performance:** Pass (sparse 2 FPS sampling avoids decoding 100% of frames). **Observability:** Pass (logs fallback mode). **Config:** Pass (`settings.SMOOTHING_FACTOR`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Sparse temporal sampling with spatial face clustering and even pixel constraints (Recommended). Option 2: Static center crop (Amateur; cuts subjects off when off-center). |
| Priority / Effort | P3 / S |
