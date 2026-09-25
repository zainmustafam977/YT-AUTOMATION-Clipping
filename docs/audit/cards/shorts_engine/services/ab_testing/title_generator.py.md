# Assessment Cards: shorts_engine/services/ab_testing/title_generator.py

---

## Unit 1: TitleArchetype & TitleVariantSet
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ab_testing/title_generator.py:21-40` |
| What it does | Defines the 4 viral title archetypes (`curiosity_gap`, `question_hook`, `bold_statement`, `numbered_listicle`) and encapsulates them in a Pydantic schema with strict 100-character YouTube Shorts length validation. |
| Industrial-grade? | **A** - Clean, strongly typed schema used for structured LLM parsing. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (Pydantic `max_length=100`). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pydantic schema model (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: TitleGenerator
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ab_testing/title_generator.py:44-138` |
| What it does | Generates 4 title variants for a Short candidate using the multi-provider `LLMRouter` (Groq -> Gemini -> OpenRouter -> OpenAI). Automatically switches to a deterministic template-based fallback if APIs are offline or unconfigured. |
| Industrial-grade? | **A** - High-resilience generator combining structured LLM output with instant deterministic fallback. |
| Checklist | **Error handling:** Pass (gracefully catches router exceptions and uses fallback). **Timeouts:** Pass (enforced by LLMRouter). **Retries:** Pass (LLMRouter failover chain). **Idempotency:** Pass. **Input validation:** Pass (cleans and truncates topics). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (multi-provider LLM with offline fallback). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Multi-tier LLM with deterministic rule fallback (Recommended). |
| Priority / Effort | P3 / S |
