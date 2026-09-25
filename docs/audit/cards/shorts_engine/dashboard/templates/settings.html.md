# Assessment Cards: shorts_engine/dashboard/templates/settings.html

---

## Unit 1: Architecture, AI Providers & Caption Styling Inputs
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/settings.html:1-200` |
| What it does | Renders the primary pipeline settings form: multi-tier AI routing controls (Auto, Groq, Gemini, OpenRouter, OpenAI), failover toggles, Groq token limits, candidate bounds (min/max duration, count), and typography presets (Hormozi, MrBeast, Neon, Minimalist, High Contrast Box) with vertical margin configuration. |
| Industrial-grade? | **A** - Comprehensive parameter controls with clear visual bounds badges and tooltips. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (HTML5 numeric min/max constraints matching backend bounds). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF token passed). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Bootstrap 5 dark forms with client-side bounds badges (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: Video & Audio Mastering & Groq Key Pool
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/settings.html:201-356` |
| What it does | Configures studio encoding quality: resampling algorithms (Lanczos, Bicubic, Spline), CRF video quality, ffmpeg presets, target FPS, audio bitrates (up to 320k), and sample rates (48000 Hz). Displays the Groq API key rotation pool with masked key fingerprints, call counters, cooldown indicators, and testing buttons. |
| Industrial-grade? | **A** - High-fidelity media encoding options coupled with clear credential pool inspection. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (keys masked, never displayed in plain text). **Performance:** Pass. **Observability:** Pass (displays request counters and cooldown states). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Masked credential pool cards with live action buttons (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: Multimodal Provider Pools (Gemini, OpenRouter, OpenAI)
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/settings.html:357-554` |
| What it does | Displays credential management pools for Google Gemini (with pacing indicators), OpenRouter, and OpenAI. Supports single-key addition, deletion with confirmation prompts, live connectivity testing, and bulk textarea entry. |
| Industrial-grade? | **A** - Flexible credential rotation management across multiple LLM backends. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (guards against empty strings). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (keys masked with SHA-256 fingerprint references). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard credential pool list with live action hooks (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 4: Computer Vision, Whisper Tuning & JavaScript Controllers
| Field | Content |
|---|---|
| Location | `shorts_engine/dashboard/templates/settings.html:555-756` |
| What it does | Controls MediaPipe face tracking confidence, AI temperature, YouTube privacy defaults, and fine-grained Whisper tuning (beam size, language override, temperature, Silero VAD filter). Contains client JavaScript: `testApiKey` for live upstream pings with millisecond latency feedback, and `removeApiKey` / `addApiKey` dynamic form generators with CSRF tokens. |
| Industrial-grade? | **A** - Granular AI engine tuning combined with responsive client-side verification. |
| Checklist | **Error handling:** Pass (handles fetch failure and updates badge). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (CSRF tokens attached to all dynamic forms). **Performance:** Pass. **Observability:** Pass (displays live ping latency). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Dynamic key test and mutation handlers with CSRF protection (Recommended). |
| Priority / Effort | P3 / S |
