# Assessment Cards: shorts_engine/services/clip_selection/prompts.py

---

## Unit 1: Pydantic Response Schemas (TitleHookVariation, CandidateSegmentSchema, CandidateSegmentsResponse)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/prompts.py:9-39` |
| What it does | Strict Pydantic v2 schemas defining structured LLM output: timestamps, verbatim hook and payoff phrases, viral score bounded [0.0, 1.0], suggested title/description/tags, A/B variations, and B-roll keywords. |
| Industrial-grade? | **A** - Enforces bounds validation and detailed field descriptions for OpenAI/Gemini/Groq JSON schema generators. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (`ge=0.0, le=1.0`). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`pydantic`). **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pydantic BaseModel schemas with Field metadata (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: GENERATOR_CRITIC_PROMPT Template
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/prompts.py:42-55` |
| What it does | Prompt engineering template embedding a 4-point editorial rubric (Hook Strength, Standalone Autonomy, Payoff & Resolution, Duration Fit) parameterized by duration bounds and transcript text. |
| Industrial-grade? | **A-** - High-retention editorial prompt forcing verbatim dialogue extraction to prevent hallucinations. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (prompt injection risk minimized by strict structured JSON response schema). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If `transcript_text` contains literal curly braces `{}` (e.g. from programming code discussions), calling `.format()` on the template string raises a `KeyError`. |
| Improvement | Escape curly braces in `transcript_text` (`replace("{", "{{").replace("}", "}}")`) before calling `.format()`. |
| Better approach? | Option 1: String format with brace escaping (Recommended). Option 2: Plain `.format()` (Current - edge case risk on technical transcripts). |
| Priority / Effort | P2 / S |
