# Assessment Cards: shorts_engine/services/ab_testing/analytics_ingestion.py

---

## Unit 1: VideoPerformanceMetrics
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ab_testing/analytics_ingestion.py:14-48` |
| What it does | Pydantic data model encapsulating Tier 1 (views, likes, comments) and Tier 2 (average view duration, audience retention percentage) metrics with automated properties for engagement rate calculation and weighted composite scoring. |
| Industrial-grade? | **A** - Clean, strongly typed metrics container with proportional fallback weighting when retention metrics are unavailable. |
| Checklist | **Error handling:** Pass (zero-division protection in calculations). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Pydantic model with composite score property (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: AnalyticsIngestionService._fetch_tier1 & _fetch_tier2
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ab_testing/analytics_ingestion.py:49-142` |
| What it does | Ingests performance metrics in two tiers: Tier 1 fetches immediate view and interaction counts via YouTube Data API v3; Tier 2 queries the YouTube Analytics API for deep retention, gracefully degrading to Tier 1 when tokens lack `yt-analytics.readonly` scope or reports have not settled (24-48h processing lag). |
| Industrial-grade? | **B** - Excellent graceful degradation on missing permissions, but hardcodes static calendar year query dates. |
| Checklist | **Error handling:** Pass (gracefully catches 403 Forbidden and missing scopes). **Timeouts:** Pass. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (YouTube Data & Analytics APIs). **Security:** Pass. **Performance:** Pass. **Observability:** Pass (clear warning logs explaining degradation). **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | `startDate="2026-01-01"` and `endDate="2026-12-31"` (lines 113-114) are hardcoded static calendar year strings, which will fail to fetch analytics in other calendar years. |
| Improvement | Compute dynamic query dates using `datetime.utcnow().date()`: `startDate=(datetime.utcnow() - timedelta(days=90)).strftime('%Y-%m-%d')` and `endDate=datetime.utcnow().strftime('%Y-%m-%d')`. |
| Better approach? | Option 1: Dynamic relative date window (Recommended). Option 2: Hardcoded calendar year (Current - defect). |
| Priority / Effort | P2 / S |

---

## Unit 3: AnalyticsIngestionService.rank_archetypes
| Field | Content |
|---|---|
| Location | `shorts_engine/services/ab_testing/analytics_ingestion.py:143-158` |
| What it does | Sorts and ranks title archetypes by their composite score descending to provide feedback loops for prompt rubric tuning. |
| Industrial-grade? | **A** - Clean analytical ranking function. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass (fast list sorting). **Observability:** Pass. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Composite score ranking (Recommended). |
| Priority / Effort | P3 / S |
