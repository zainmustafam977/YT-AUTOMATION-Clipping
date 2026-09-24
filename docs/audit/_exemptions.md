# Audit Exemptions Register

This register documents all files and patterns excluded from direct textual line-by-line reading in accordance with the Phase 0 charter:
- Only binary assets (images, fonts, media, neural network weights), VCS internals, lockfiles (analyzed via dependency tooling), and temporary test media artifacts are exempted.
- Every textual source file, configuration file, SQL script, template, CI/CD pipeline, and test file is included and strictly audited line-by-line via `tools/audit/read_chunk.py`.

---

| Pattern / File | Classification | Exact Reason for Exemption |
| :--- | :--- | :--- |
| `storage/models/face_detection_yunet.onnx` | Neural Net Weights | Pre-trained ONNX binary weights for OpenCV YuNet face detection. Analyzed via OpenCV DNN API rather than text parser. |
| `storage/backups/*.db.gz` | Compressed Binary | Gzip-compressed binary SQLite hot database snapshots. |
| `storage/backups/*.sha256` | Checksum Manifest | Auto-generated SHA256 checksum strings for backup verification. |
| `storage/shorts_automation.db` | Binary Database | SQLite relational database storage container file. Schema is textually audited in `shorts_engine/db/models.py`. |
| `storage/shorts_automation.db-wal` | SQLite WAL Journal | SQLite Write-Ahead Log binary database internal file. |
| `storage/shorts_automation.db-shm` | SQLite Shared Memory | SQLite shared-memory index binary file. |
| `storage/downloads/**` | Media Asset (Video) | Downloaded raw YouTube source MP4 video streams. |
| `storage/outputs/**` | Media Asset (Video) | Rendered 9:16 vertical MP4 video output files. |
| `storage/thumbnails/**` | Image Asset (JPEG) | Extracted JPEG frame thumbnails for dashboard review queue. |
| `storage/temp/**/*.wav` | Audio Asset (WAV) | Extracted 16kHz mono PCM WAV audio buffers. |
| `storage/temp/**/*.part*` | Partial Media Fragment | Temporary FFmpeg / yt-dlp stream fragments. |
| `assets/sfx_library/**` | Audio Assets (WAV/MP3)| Contextual sound effect library audio files. |
| `YouTube Shorts Automation - Project Handover Spec.docx` | Binary Document | Microsoft Word binary container. Textually extracted to `spec_extracted.md` (which is fully audited). |
| `*.pyc` | Bytecode Cache | Python compiled bytecode cache. |
| `__pycache__/**` | Bytecode Directory | Python compiled bytecode directories. |
| `.git/**` | VCS Internals | Git repository internals and object database. |
| `docs/audit/**` | Audit Output Artifacts | Dedicated output directory containing the audit reports, ledger, state, and cards. |
| `scratch_queue.html` | Test HTML Snapshot Dump | Ephemeral browser test capture; underlying Jinja2 template `shorts_engine/dashboard/templates/queue.html` is 100% audited line-by-line. |
| `scratch/**` | Ephemeral Tool Outputs | Temporary tool execution dumps (e.g., gitleaks JSON outputs); not application source. |
