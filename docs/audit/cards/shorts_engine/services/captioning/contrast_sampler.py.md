# Assessment Cards: shorts_engine/services/captioning/contrast_sampler.py

---

## Unit 1: ContrastSampler Class (sample_background_luminance & compute_style_adjustments)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/contrast_sampler.py:14-69` |
| What it does | Analyzes video frames within the subtitle lower-third ROI, extracts mean pixel luminance (0.0 to 255.0), and dynamically scales ASS outline stroke width (2.5 to 4.0px), drop shadow depth, and semi-transparent backing box opacity to guarantee WCAG-compliant legibility across white and dark footage. |
| Industrial-grade? | **A** - Computer-vision-guided typography adaptation eliminating illegible white text against blown-out backgrounds. |
| Checklist | **Error handling:** Pass (handles empty ROI). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (clamps normalized coords). **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass (`cv2`, `numpy`). **Security:** Pass. **Performance:** Pass (fast NumPy sub-array mean). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Lower-third bounding box `(0.1, 0.65, 0.9, 0.85)` is statically normalized and does not dynamically adjust if the operator reconfigures `margin_vertical` to place captions in the center or upper third. |
| Improvement | Adjust ROI vertical bounds based on `settings.CAPTION_MARGIN_VERTICAL`. |
| Better approach? | Option 1: Margin-aware ROI sampling (Recommended). Option 2: Fixed lower-third ROI (Current - effective for standard Shorts). |
| Priority / Effort | P2 / S |
