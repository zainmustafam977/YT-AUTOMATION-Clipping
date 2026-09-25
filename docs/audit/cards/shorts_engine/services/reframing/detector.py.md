# Assessment Cards: shorts_engine/services/reframing/detector.py

---

## Unit 1: DetectedFace Data Model
| Field | Content |
|---|---|
| Location | `shorts_engine/services/reframing/detector.py:22-41` |
| What it does | Data structure encapsulating 2D bounding box coordinates `(x, y, w, h)`, confidence score, bounding box area, and geometric centroid properties `(center_x, center_y)`. |
| Industrial-grade? | **A** - Clean geometry carrier for face coordinates. |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Keep as is (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: SubjectDetector Class (_ensure_model & detect_faces)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/reframing/detector.py:43-104` |
| What it does | High-speed neural face detection wrapping OpenCV's official YuNet ONNX deep-learning model (`cv2.FaceDetectorYN`), with automatic downloading of lightweight (~300KB) weights and dynamic frame input sizing. |
| Industrial-grade? | **A** - Sub-millisecond CPU inference (~300 FPS), vastly outperforming brittle legacy Haar cascades in rotation, scale, and lighting invariance. |
| Checklist | **Error handling:** Pass (catches detector failures, falls back gracefully). **Timeouts:** Warning (`urlretrieve` lacks explicit timeout). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (guards array dimensions). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass (`cv2.FaceDetectorYN`). **Security:** Pass (fetches from official OpenCV Zoo GitHub). **Performance:** Pass (fast C++ ONNX engine). **Observability:** Pass. **Config:** Pass (`settings.MODELS_DIR`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | `urllib.request.urlretrieve` does not specify a socket timeout. If GitHub connection hangs on initial download, cold start could block. |
| Improvement | Use `urllib.request.urlopen(req, timeout=30)` with stream writing to enforce a download timeout. |
| Better approach? | Option 1: OpenCV DNN YuNet with download timeout (Recommended). Option 2: Legacy Haar cascades (Deprecated; high false positive rate). |
| Priority / Effort | P2 / S |
