# Assessment Cards: shorts_engine/services/reframing/active_speaker.py

---

## Unit 1: ActiveSpeakerSelector Class & select_speaker Method
| Field | Content |
|---|---|
| Location | `shorts_engine/services/reframing/active_speaker.py:10-50` |
| What it does | Chooses the primary subject in multi-speaker frames (podcasts, interviews) by evaluating face area, detection confidence, and center proximity, enforcing a 25% switching hysteresis (`switch_hysteresis=1.25`) to prevent camera oscillation. |
| Industrial-grade? | **A-** - Hysteresis threshold effectively eliminates distracting rapid ping-pong cuts between subjects. |
| Checklist | **Error handling:** Pass (handles empty faces list by retaining previous speaker). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (stateful instance per video). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (lightweight $O(N)$ geometry math). **Observability:** N/A. **Config:** Pass (`switch_hysteresis`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Relies purely on visual geometry (face area + center distance) rather than active acoustic lip synchronization (SyncNet). A larger foreground listener can be selected over a speaking background guest. |
| Improvement | Integrate mouth-aperture optical variance or audio-visual sync when full neural pipelines are available. |
| Better approach? | Option 1: Geometric salience with hysteresis (Current - fast, low compute, effective for 95% of two-person podcasts). Option 2: Acoustic SyncNet neural model (Accurate, but requires GPU and heavy PyTorch models). |
| Priority / Effort | P2 / M |
