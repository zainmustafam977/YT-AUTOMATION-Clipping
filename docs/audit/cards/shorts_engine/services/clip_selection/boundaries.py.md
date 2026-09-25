# Assessment Cards: shorts_engine/services/clip_selection/boundaries.py

---

## Unit 1: Linguistic Boundary Filter Constants (BANNED_START_WORDS, BANNED_END_WORDS)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/boundaries.py:13-25` |
| What it does | Defines blacklists of grammatical tokens that produce jarring, ungrammatical clip boundaries: conjunctions/relative pronouns for start tokens (`which`, `and`, `because`) and auxiliary verbs/prepositions for end tokens (`is`, `would`, `with`, `the`, `didn't`). |
| Industrial-grade? | **A** - Comprehensive linguistic stoplists preventing mid-thought truncation. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (immutable sets). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass ($O(1)$ set membership). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Immutable set blacklists (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: BoundarySnapper Class & snap_boundaries Method
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/boundaries.py:28-159` |
| What it does | Snaps raw AI-generated clip boundaries to clean grammatical sentence boundaries and acoustic silence pauses ($\ge 0.4$s), advances past banned starting conjunctions, evaluates candidate endpoints across 4 prioritized tiers (sentence punctuation $\to$ clause punctuation $\to$ silence $\to$ clean words), and applies $+1.5$s duration elasticity aligned with the Technical Quality Gate. |
| Industrial-grade? | **A** - High-grade acoustic and syntactic alignment ensuring clips begin with complete thoughts and end with natural payoffs. |
| Checklist | **Error handling:** Pass (handles empty words list). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass ($O(N)$ single pass over window). **Observability:** Pass. **Config:** Pass (`min_duration_sec`, `max_duration_sec`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If a candidate segment occurs in non-stop, run-on speech without any punctuation or silence gaps, fallback picks the closest word which might be slightly short of `min_duration_sec`. |
| Improvement | Explicitly enforce `end_ms - start_ms >= min_duration_sec * 1000` in the fallback return path. |
| Better approach? | Option 1: Multi-tier linguistic/acoustic snapping with duration clamp (Recommended). Option 2: Hard millisecond truncation (Amateur; slices words in half). |
| Priority / Effort | P2 / S |
