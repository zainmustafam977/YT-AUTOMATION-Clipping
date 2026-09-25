# Assessment Cards: shorts_engine/services/captioning/templates.py

---

## Unit 1: Color Converters (hex_to_ass_color, ass_to_hex_color)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/templates.py:13-52` |
| What it does | Bidirectionally translates between standard web hex colors (`#RRGGBB`) and libass subtitle color format (`&H00BBGGRR`), correctly swapping red and blue channels. |
| Industrial-grade? | **A-** - Accurately handles 3-digit shorthand, 6-digit RGB, and existing `&H` prefixes. |
| Checklist | **Error handling:** Pass (returns white fallback on invalid format). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure functions). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | For 8-digit `#AARRGGBB`, web CSS treats `FF` as 100% opaque, whereas libass treats `&H00` as 100% opaque and `&HFF` as fully transparent. Web alpha is not inverted. |
| Improvement | Invert alpha byte (`255 - int(a, 16)`) when parsing 8-digit web hex colors. |
| Better approach? | Option 1: Invert alpha byte for 8-digit hex (Recommended). Option 2: Direct byte copy (Current - standard 6-digit hex works flawlessly). |
| Priority / Effort | P3 / S |

---

## Unit 2: CaptionStyleTemplate Class
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/templates.py:54-101` |
| What it does | Pydantic v2 schema defining subtitle typography, colors, outline width, shadow depth, vertical baseline margin, words per line, and text casing, with dual mapping methods for ASS generator and web UI previews. |
| Industrial-grade? | **A** - Complete, validated domain model unifying backend rendering and frontend CSS previews. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`pydantic`). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pydantic domain template (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: Built-in Preset Library & Accessors (BUILTIN_TEMPLATES, get_caption_template, list_caption_templates, parse_custom_template)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/templates.py:104-214` |
| What it does | Curated library of 5 industry presets (Hormozi, MrBeast, Cyberpunk Neon, Minimalist Editorial, High Contrast Box), safe retrieval with fallback, and operator custom preset parser. |
| Industrial-grade? | **A** - Professional aesthetic presets providing out-of-the-box viral typography without manual subtitle styling. |
| Checklist | **Error handling:** Pass (falls back to Hormozi preset if unknown ID passed). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (safely parses operator custom JSON). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass (`settings.DEFAULT_CAPTION_PRESET`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Curated preset registry with custom JSON parser (Recommended). |
| Priority / Effort | P3 / S |
