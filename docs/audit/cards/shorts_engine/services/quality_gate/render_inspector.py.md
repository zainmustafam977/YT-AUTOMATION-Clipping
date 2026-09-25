# Assessment Cards: shorts_engine/services/quality_gate/render_inspector.py

---

## Unit 1: RenderInspector.__init__
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/render_inspector.py:18-20` |
| What it does | Initializes the post-render inspector with an underlying `SubjectDetector` instance for OpenCV/DNN face detection. |
| Industrial-grade? | **A** - Clean initialization. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard composition (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: RenderInspector.inspect_render
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/render_inspector.py:22-103` |
| What it does | Samples 5 evenly distributed keyframes (10%, 30%, 50%, 70%, 90%) using OpenCV. Evaluates face centering against horizontal safe margins (15%-85%) and applies Canny edge detection across YouTube Shorts UI dead zones (bottom title bar Y > 80%, right-side action buttons X > 85%, Y > 40%) to detect subtitle bleed. |
| Industrial-grade? | **B+** - Computer vision QA inspection ensuring mobile UI readability, but lacks guaranteed OpenCV handle release on error and can trigger false positives on high-frequency background textures. |
| Checklist | **Error handling:** Pass (catches unopenable video and 0-frame videos). **Timeouts:** Pass (fast 5-frame seek). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Needs improvement (`cap.release()` is not in a `finally` block). **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (OpenCV / NumPy). **Security:** Pass. **Performance:** Pass (sparse sampling 5 frames avoids full-length decoding). **Observability:** Pass (returns violation counts and frame diagnostics). **Config:** Pass (reads settings dead-zone ratios). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | 1. `cap.release()` is called after the loop without a `try...finally` block; an unhandled exception in OpenCV or detector leaks the open file handle. 2. Edge density in the bottom dead zone can exceed 0.15 on naturally textured backgrounds (e.g. gravel, crowd, grass), creating false-positive collision flags. |
| Improvement | 1. Wrap the frame reading and inspection loop in `try...finally: cap.release()`. 2. Filter edge detection with text-specific characteristics (e.g. horizontal stroke connectivity or white/yellow color masks) rather than raw generic edge density. |
| Better approach? | Option 1: `try/finally` cleanup + text-color masked edge inspection (Recommended). Option 2: Generic Canny edge detection without `finally` (Current). |
| Priority / Effort | P2 / S |
