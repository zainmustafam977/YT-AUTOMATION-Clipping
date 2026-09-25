# Module Reference: 12 Multi-Platform Syndication, Drip-Feed & Bayesian A/B Title Optimization

> **Path:** `shorts_engine/services/publishing/` & `shorts_engine/services/ab_testing/`  
> **Source Files:**
> - [`shorts_engine/services/publishing/base.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/base.py)
> - [`shorts_engine/services/publishing/client.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/client.py)
> - [`shorts_engine/services/publishing/manager.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/manager.py)
> - [`shorts_engine/services/publishing/drip_feed.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/drip_feed.py)
> - [`shorts_engine/services/publishing/quota.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/quota.py)
> - [`shorts_engine/services/publishing/tiktok.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/tiktok.py)
> - [`shorts_engine/services/publishing/instagram.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/publishing/instagram.py)
> - [`shorts_engine/services/ab_testing/bandit.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/bandit.py)
> - [`shorts_engine/services/ab_testing/winner_daemon.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/winner_daemon.py)
> - [`shorts_engine/services/ab_testing/title_generator.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/title_generator.py)
> - [`shorts_engine/services/ab_testing/analytics_ingestion.py`](file:///d:/Projects/YT%20AUTOMATION/shorts_engine/services/ab_testing/analytics_ingestion.py)

---

## 1. Module Purpose & Responsibilities

This subsystem handles scheduled social publishing, multi-platform syndication, quota budgets, and automated headline optimization:
1. **Multi-Platform Strategy Pattern:** Implements `BasePlatformPublisher` strategy decoupling platform-specific APIs (YouTube Shorts, TikTok Content Posting API v2, Instagram Reels Graph API v19.0) from orchestration and DB logging (`base.py:39-88`, `manager.py:95-220`).
2. **YouTube Data API v3 Client:**
   - Decrypts AES-256-GCM OAuth tokens stored on channel records (`client.py:22-43, 157-158`).
   - Executes resumable chunked video uploads (256KB chunks) via `videos().insert()` (`client.py:45-88`).
   - Guarantees strict idempotency: rejects attempts to republish clips already marked `PUBLISHED` (`client.py:108-116`).
   - Supports dry-run simulation mode (`DRY_RUN_PUBLISHING=true`) generating mock video IDs (`dry_run_<clip_id[:8]>`) (`client.py:139-143`).
3. **Daily API Quota Management:** Tracks YouTube API quota points (`UPLOAD_COST_UNITS = 1600` out of 10,000 daily limit). Reconciles state against `PublishLog` database records and queues jobs when quota is exhausted (`quota.py:18-99`).
4. **Scheduled Drip-Feed Daemon:** Polls for approved clips where `scheduled_publish_at <= now()`, constructs metadata, and publishes them sequentially (`drip_feed.py:45-100`).
5. **TikTok & Instagram Reels Adapters:**
   - **TikTok:** Resumable chunked uploading (10MB chunks), creator privacy checks, and async publishing status polling (`tiktok.py:27-277`).
   - **Instagram:** Container initialization, processing state machine polling, and feed publishing (`instagram.py:26-275`).
6. **Bayesian Multi-Armed Bandit Title Optimization:**
   - Models Click-Through Rate (CTR) using Beta-Binomial conjugate updating (`prior_alpha=1.0, prior_beta=19.0`, expressing a 5% baseline CTR prior) (`bandit.py:58-86`).
   - Computes $P(\text{Variant } B > \text{Variant } A)$ via 15,000-sample Monte Carlo simulations (`bandit.py:89-97`).
   - **Automated Winner Promotion:** Switches live YouTube titles via `videos().update()` when confidence reaches $\ge 95\%$ (`winner_daemon.py:61-100`).
   - **Emergency Circuit Breaker:** Automatically rolls back to baseline headline if hourly view velocity drops by $>50\%$ (`bandit.py:71-75`, `winner_daemon.py:102-140`).
7. **Archetypal Title Generator:** Generates 4 CTR-optimized headline variations (Curiosity Gap, Question Hook, Bold Statement, Numbered Listicle) strictly bounded to 100 characters (`title_generator.py:21-49`).
8. **Two-Tier Analytics Ingestion:** Ingests real-time statistics (views, likes, comments) and deep retention metrics (average view percentage) (`analytics_ingestion.py:14-159`).

---

## 2. Public Interfaces & Signatures

### 2.1 `shorts_engine.services.publishing.base.BasePlatformPublisher`
- `PlatformPublishResult(platform, success, post_id, permalink, error, metadata, published_at, dry_run)` (`base.py:14-37`).
- `publish(video_path, title, description, tags=None, scheduled_time=None, dry_run=None, **kwargs) -> PlatformPublishResult` (`base.py:52-77`).
- `health_check() -> Tuple[bool, str]` (`base.py:80-87`).

### 2.2 `shorts_engine.services.publishing.client.YouTubePublisher`
- `publish_clip(clip: Clip, metadata: ShortsMetadata, dry_run=None, encrypted_oauth_token=None) -> Dict[str, Any]` (`client.py:95-193`).
- `update_video_title(youtube_video_id: str, new_title: str, encrypted_oauth_token=None, dry_run=None) -> Dict[str, Any]` (`client.py:194-254`).

### 2.3 `shorts_engine.services.publishing.drip_feed`
- `build_clip_metadata(clip: Clip) -> ShortsMetadata`: Extracts title and hashtags (`drip_feed.py:23-43`).
- `check_and_publish_due_clips(db: Session) -> List[str]`: Drip-feed batch runner (`drip_feed.py:45-100`).

### 2.4 `shorts_engine.services.publishing.quota.QuotaTracker`
- `can_publish(db=None) -> Tuple[bool, int, str]`: Checks if $\ge 1,600$ units remain (`quota.py:55-76`).
- `record_publish(cost=1600, db=None) -> int` (`quota.py:92-99`).

### 2.5 `shorts_engine.services.ab_testing.bandit.BayesianVariantOptimizer`
- `calculate_posterior_parameters(clicks: int, impressions: int) -> Tuple[float, float]` (`bandit.py:77-86`).
- `probability_b_beats_a(alpha_a, beta_a, alpha_b, beta_b, n_samples=15000) -> float`: Monte Carlo Beta test (`bandit.py:89-97`).
- `evaluate_variations(variations, current_metrics, baseline_velocity, current_velocity) -> VariantEvaluationResult` (`bandit.py:110-185`).

### 2.6 `shorts_engine.services.ab_testing.winner_daemon`
- `evaluate_and_promote_clip(db: Session, clip: Clip, optimizer=None, publisher=None) -> Optional[VariantEvaluationResult]` (`winner_daemon.py:25-130`).

---

## 3. Syndication & A/B Testing Topology

```mermaid
flowchart TD
    subgraph Drip_Feed_Scheduler
        CRON[Timer / Worker Loop] --> DUE[drip_feed.check_and_publish_due_clips]
        DUE --> FIND[Query DB: status == APPROVED & scheduled_at <= now]
        FIND --> PUB[YouTubePublisher.publish_clip]
    end

    subgraph Quota_Guards
        PUB --> QCHK{QuotaTracker.can_publish >= 1600 units?}
        QCHK -->|No| QUEUE_LATER[Postpone & Alert Operator]
        QCHK -->|Yes| DRY_CHK{DRY_RUN_PUBLISHING == true?}
    end

    subgraph Multi_Platform_Syndication [Parallel Syndication Hub]
        DRY_CHK -->|Yes| SIM[Emit Mock Publish ID & Set PUBLISHED]
        DRY_CHK -->|No| LIVE_PUB[Decrypt Channel OAuth Secret via AES-256-GCM]
        LIVE_PUB --> YT_UP[YouTube Data API: Resumable Upload 256KB Chunks]
        LIVE_PUB --> TT_UP[TikTok Content Posting API: 10MB Chunks]
        LIVE_PUB --> IG_UP[Instagram Reels Graph API: Container Init & Publish]
    end

    YT_UP --> DB_LOG[Atomically write PublishLog & PlatformPublishLog]
    SIM --> DB_LOG

    subgraph Closed_Loop_AB_Testing [Bayesian A/B Promotion Daemon]
        DB_LOG --> STATS[AnalyticsIngestionService: Views, Clicks, Retention]
        STATS --> OPT[BayesianVariantOptimizer.evaluate_variations]
        OPT --> BETA[Conjugate Beta Posterior & 15k Monte Carlo Samples]
        
        BETA --> WIN{P(B > A) >= 0.95?}
        WIN -->|Yes| PROMOTE[Update Live Title on YouTube API videos.update]
        WIN -->|No| CB{View Velocity dropped > 50%?}
        CB -->|Yes| ROLLBACK[Circuit Breaker: Rollback to Baseline Headline]
        CB -->|No| WAIT[Maintain Current Variant & Keep Monitoring]
    end
```

---

## 4. Invariants, Failure Recovery & Circuit Breakers

1. **Strict Idempotency:** If `publish_clip()` receives a clip with `status == ClipStatus.PUBLISHED`, it halts immediately and returns the cached `PublishLog` record, guaranteeing zero duplicate uploads (`client.py:108-116`).
2. **Quota Safety Guard:** If the daily quota budget falls below 1,600 units, publishing is refused (`can_pub = False`), safeguarding remaining quota for channel queries and analytics (`quota.py:63-70`).
3. **Bayesian Statistical Confidence Threshold:** A title variation is never promoted unless its posterior probability of outperforming the baseline is $\ge 95\%$ ($P(B > A) \ge 0.95$) over at least 100 impressions (`bandit.py:61-70`).
4. **Velocity Collapse Circuit Breaker:** If a promoted headline triggers a $>50\%$ drop in view velocity compared to baseline, the optimizer immediately trips `circuit_breaker_triggered = True` and calls `videos().update()` to revert to the baseline title (`bandit.py:71-75`, `winner_daemon.py:102-140`).
