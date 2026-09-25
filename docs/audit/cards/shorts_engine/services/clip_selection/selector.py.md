# Assessment Cards: shorts_engine/services/clip_selection/selector.py

---

## Unit 1: parse_ai_provenance Helper Function
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/selector.py:26-95` |
| What it does | Parses structured metadata tags `[provider:model:latency:failover]` embedded in candidate reason strings, returning structured provenance (provider, model, latency in ms, failover flag, UI badge icon, clean reason). |
| Industrial-grade? | **A** - Clean regex with legacy heuristic fallbacks, ensuring full observability across all AI tiers in the review queue UI. |
| Checklist | **Error handling:** Pass (handles None and unformatted strings). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Embedded tag parsing with legacy fallback (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: SelectedClip Class
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/selector.py:97-160` |
| What it does | Rich domain entity encapsulating millisecond boundaries, duration, score, hook/payoff text, suggested title/description/tags, full transcript segment, and parsed AI provenance. |
| Industrial-grade? | **A** - Complete, strongly typed clip representation with dictionary serialization for API/dashboard consumption. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (`__repr__`). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 3: ClipSelectionService.__init__ & _build_transcript_prompt_text
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/selector.py:162-203` |
| What it does | Initializes LLM router and boundary snapper, and formats word transcripts into grouped ~10-15s timestamped dialogue paragraphs for LLM context compression. |
| Industrial-grade? | **A** - Grouping words into timestamped chunks drastically reduces prompt token size compared to raw JSON word dumps. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (handles empty transcript). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (token compression). **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Timestamp-chunked dialogue grouping (Recommended - optimal LLM token efficiency). |
| Priority / Effort | P3 / S |

---

## Unit 4: ClipSelectionService.select_clips
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/selector.py:204-336` |
| What it does | Master clip selection pipeline: invokes `LLMRouter.generate_structured` with per-video runtime overrides, tags AI provenance metadata, falls back to `_heuristic_select` if all APIs fail, snaps boundaries via `BoundarySnapper`, and validates duration bounds. |
| Industrial-grade? | **A** - Production-grade resilience: automated multi-tier AI execution, transparent heuristic fallback, linguistic boundary snapping, and verbatim hook/payoff verification. |
| Checklist | **Error handling:** Pass (graceful fallback on LLM failure). **Timeouts:** Pass (inherited from router). **Retries:** Pass (multi-tier cloud failover). **Idempotency:** Pass. **Input validation:** Pass (handles empty transcripts, respects bounds). **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass (detailed provenance logging). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: AI structured generation with boundary snapping and heuristic circuit breaker (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 5: ClipSelectionService._heuristic_select
| Field | Content |
|---|---|
| Location | `shorts_engine/services/clip_selection/selector.py:337-430` |
| What it does | Algorithmic circuit-breaker fallback: scans transcript using sentence density, question mark triggers (`?`), and high-energy vocabulary (`secret`, `truth`, `warning`, `why`), strips opening filler words (`And`, `So`, `Like`), and extracts verbatim dialogue for hooks and payoffs. |
| Industrial-grade? | **A** - Zero-cost, 100% deterministic offline fallback ensuring pipeline never halts even during global cloud AI outages. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass (deterministic algorithm). **Input validation:** Pass (handles short/empty transcripts). **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass (zero external dependencies). **Security:** Pass. **Performance:** Pass (fast single-pass loop). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Sentence-density and question-driven dialogue heuristic (Recommended). Option 2: Hard-split fixed 30s chunks (Poor; slices sentences randomly). |
| Priority / Effort | P3 / S |
