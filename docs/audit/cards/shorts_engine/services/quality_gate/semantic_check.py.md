# Assessment Cards: shorts_engine/services/quality_gate/semantic_check.py

---

## Unit 1: SemanticVerificationSchema
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/semantic_check.py:18-24` |
| What it does | Pydantic data schema for structured LLM semantic evaluation: parses standalone autonomy, unresolved references, payoff delivery, pass/fail decision, and critique reasoning. |
| Industrial-grade? | **A** - Clean, strongly typed schema used for Gemini/Groq JSON mode extraction. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pydantic structured output model (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: SemanticVerifier.__init__
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/semantic_check.py:26-43` |
| What it does | Initializes the verifier with configured model name, API keys, and an `LLMRouter` instance supporting multi-tier LLM failover. |
| Industrial-grade? | **A** - Clean composition with router dependency injection support. |
| Checklist | **Error handling:** Pass. **Timeouts:** Pass. **Retries:** Pass. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (no hardcoded secrets). **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Current router pattern (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: SemanticVerifier.verify_segment
| Field | Content |
|---|---|
| Location | `shorts_engine/services/quality_gate/semantic_check.py:44-125` |
| What it does | Evaluates Short transcript for standalone autonomy, hook-payoff delivery, and prohibited sponsor/continuity phrases. Calls LLM via structured schema with automatic heuristic rule-based fallback if the API is offline. |
| Industrial-grade? | **A-** - High-resilience validation with rule-based fallback when external LLMs are unavailable, but prohibited phrases are hardcoded in the function. |
| Checklist | **Error handling:** Pass (graceful fallback to heuristics if LLM call returns None). **Timeouts:** Pass (enforced by LLMRouter). **Retries:** Pass (handled by LLMRouter multi-tier retry/failover). **Idempotency:** Pass. **Input validation:** Pass (checks empty segment text). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (Gemini/Groq with rule-based fallback). **Security:** Pass (prompt escaping). **Performance:** Pass (fast heuristic fallback). **Observability:** Pass (returns comprehensive advisory dictionary). **Config:** Needs minor improvement (prohibited phrases hardcoded in method). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | Prohibited phrases list (`forbidden_phrases`) is hardcoded inside the function body and uses substring checking rather than word-boundary regex, risking false positives. |
| Improvement | Move prohibited phrases to `settings.PROHIBITED_TRANSITION_PHRASES` and use word-boundary regex patterns (`\bphrase\b`). |
| Better approach? | Option 1: Configurable word-boundary regex list (Recommended). Option 2: Hardcoded substring list (Current). |
| Priority / Effort | P2 / S |
