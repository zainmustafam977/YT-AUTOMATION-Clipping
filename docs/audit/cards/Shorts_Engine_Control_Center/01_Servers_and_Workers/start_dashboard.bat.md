# Assessment Cards: Shorts_Engine_Control_Center/01_Servers_and_Workers/start_dashboard.bat

---

## Unit 1: Web Dashboard Launcher Script
| Field | Content |
|---|---|
| Location | `Shorts_Engine_Control_Center/01_Servers_and_Workers/start_dashboard.bat:1-36` |
| What it does | Launches the FastAPI web dashboard service on port 8000 using Python 3.11. Resolves repository root, automatically spawns default web browser pointing to `http://127.0.0.1:8000/`, and executes `run.py dashboard --port 8000`. |
| Industrial-grade? | **B+** - Convenient local launcher with automatic browser opening, but contains machine-specific absolute paths in directory resolver. |
| Checklist | **Error handling:** Neutral (pauses on exit to display error tracebacks). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** N/A. **Resource cleanup:** Pass. **Concurrency safety:** Pass (can only bind port 8000 once; fails cleanly with address in use error if already running). **Atomicity:** Pass. **Secret hygiene:** Pass. **Logging:** Standard output. **Telemetry:** N/A. **Testability:** Pass. **Cost:** Free. **Modularity:** Pass. **Performance:** Pass. |
| Improvement proposals / Alternatives | Remove machine-specific hardcoded absolute paths on lines 12-16. Consider adding a short 1-2 second sleep before launching browser to ensure Uvicorn has bound the port. |
