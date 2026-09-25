# Assessment Cards: shorts_engine/services/captioning/ass_generator.py

---

## Unit 1: format_ass_timestamp Helper Function
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/ass_generator.py:14-23` |
| What it does | Formats float seconds into libass subtitle timestamp format `H:MM:SS.cc`, handling centisecond carryover when rounded centiseconds reach 100. |
| Industrial-grade? | **A** - Clean arithmetic handling centisecond rollover (`centis == 100 -> secs += 1`). |
| Checklist | **Error handling:** Pass. **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** N/A. **Concurrency safety:** Pass (pure function). **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass. **Performance:** Pass. **Observability:** N/A. **Config:** Pass. **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | None. |
| Improvement | Keep as is. |
| Better approach? | Option 1: Standard centisecond formatter with carryover (Recommended). |
| Priority / Effort | P3 / S |

---

## Unit 2: ASSSubtitleGenerator Class (generate_ass & _generate_header)
| Field | Content |
|---|---|
| Location | `shorts_engine/services/captioning/ass_generator.py:25-178` |
| What it does | Generates word-synchronized Advanced SubStation Alpha (.ass) subtitle scripts for 1080x1920 portrait canvas; chunks words into lines, applies inline active-word karaoke color tags `{\c<hex>&}`, sanitizes backslashes, and sets bottom-center alignment. |
| Industrial-grade? | **A-** - High-retention word-popping subtitle generation compliant with FFmpeg's `subtitles` filter. |
| Checklist | **Error handling:** Pass (produces valid empty header if words list is empty). **Timeouts:** N/A. **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass (sanitizes backslashes). **Resource cleanup:** Pass. **Concurrency safety:** Pass. **Atomicity:** Pass. **External-dependency:** Pass. **Security:** Pass (strips backslashes). **Performance:** Pass. **Observability:** Pass. **Config:** Pass (`settings.CAPTION_MARGIN_VERTICAL`, `CAPTION_FONT_NAME`). **Testability:** Pass. **Readability:** Pass. |
| Weakness or risk | If transcript words contain literal curly braces `{` or `}`, they could interfere with ASS override control tags. |
| Improvement | Strip or escape `{` and `}` characters from word text alongside backslashes. |
| Better approach? | Option 1: Comprehensive ASS tag escaping (Recommended). Option 2: Backslash strip only (Current - safe for standard speech). |
| Priority / Effort | P2 / S |
