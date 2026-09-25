# Assessment Cards: shorts_engine/services/reframing/smoothing.py

---

## Unit 1: CropSmoother Class (update & smooth_trajectory)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/reframing/smoothing.py:9-43` |
| What it does | Applies an Exponential Moving Average (EMA, $\alpha=0.85$) filter combined with per-frame velocity clamping (max 5% frame width per step) to eliminate jitter and produce cinematic pans during 9:16 portrait cropping. |
| Industrial-grade? | **A-** - High-utility camera damping filter preventing jarring crops. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (handles empty trajectories). **Resource cleanup:** N/A. **Concurrency safety:** Pass (stateful instance per video). **Atomicity:** Pass. **External-dependency:** Pass (pure Python). **Security:** Pass. **Performance:** Pass ($O(1)$ per frame update). **Observability:** N/A. **Config:** Pass (`alpha`, `max_step_ratio`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Pure forward EMA introduces phase lag (the virtual camera lags 2-3 frames behind rapid head turns). |
| Improvement | For pre-computed trajectories (`smooth_trajectory`), apply forward-backward zero-phase filtering (`scipy.signal.filtfilt` or two-pass EMA). |
| Better approach? | Option 1: Forward-backward zero-phase smoothing (Recommended for pre-computed offline trajectories). Option 2: Forward EMA with velocity clamp (Current - effective, minor lag). |
| Priority / Effort | P2 / S |
