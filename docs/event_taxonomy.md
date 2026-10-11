# Event Bus Taxonomy & Telemetry Specification

See [`docs/architecture/08_event_taxonomy.md`](file:///d:/Projects/YT%20AUTOMATION%20CLIPPING/YT%20AUTOMATION/docs/architecture/08_event_taxonomy.md) for full architecture notes.

## Event Taxonomy Table

| Event Name | Key Payload Fields | Primary Emitter | Level | Description |
| :--- | :--- | :--- | :--- | :--- |
| `stage.finished` | `job_id`, `stage`, `entity_id`, `entity_type`, `result` | `StageExecutor` / `WorkerDaemon` | `INFO` / `SUCCESS` | Emitted when an orchestration pipeline stage finishes execution successfully. |
| `stage.failed` | `job_id`, `stage`, `entity_id`, `entity_type`, `attempt_count`, `next_run_at`, `error` | `StageExecutor` | `WARNING` | Emitted when a stage encounters a transient error and is scheduled for backoff retry. |
| `stage.quarantined` | `job_id`, `stage`, `entity_id`, `entity_type`, `attempt_count`, `last_error` | `StageExecutor` | `ERROR` | Emitted when a stage raises `PermanentError` or exhausts maximum retries. |
| `video.ingested` | `video_id`, `source_path`, `source_type`, `title` | `watch_folder.py` / `routes/status.py` | `INFO` | Emitted when a new video file is detected and registered into the ingestion queue. |
| `clip.approved` | `clip_id`, `video_id`, `approved_by`, `title` | `routes/queue.py` (`approve_clip`) | `SUCCESS` | Emitted when an operator reviews and approves a candidate clip for publishing. |
| `clip.published` | `clip_id`, `youtube_video_id`, `channel_id`, `title`, `youtube_url` | `PublishWorker` (`execute_publish_intent`) | `SUCCESS` | Emitted when a video upload completes successfully on YouTube. |
| `channel.health` | `channel_id`, `youtube_channel_id`, `healthy`, `status`, `error` | `PublishWorker` (`execute_daily_health_intent`) | `INFO` / `ERROR` | Emitted when a channel's credentials and status are verified via `channels.list`. |
| `quota.low` | `quota_used`, `quota_limit`, `remaining_units`, `pct_used`, `reason` | `PublishWorker` / `QuotaTracker` | `WARNING` | Emitted when daily YouTube API quota consumption approaches limits or triggers a 403 deferral. |
