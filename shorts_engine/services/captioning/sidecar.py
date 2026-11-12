"""SRT and WebVTT sidecar subtitle generation.

Closes the advertised-but-missing F-12 sidecar export: the engine already
produces burned-in ASS captions; this module emits portable sidecar files
for platforms that ingest SRT/VTT tracks.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Sequence

from shorts_engine.services.transcription.formatter import WordSegment

logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float, *, vtt: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round(seconds * 1000.0))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    sep = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"


def _group_words(
    words: Sequence[WordSegment], max_words: int = 8, max_gap_sec: float = 1.0
) -> List[List[WordSegment]]:
    groups: List[List[WordSegment]] = []
    current: List[WordSegment] = []
    for w in words:
        if not w.word:
            continue
        if current:
            gap = w.start - current[-1].end
            if len(current) >= max_words or gap > max_gap_sec:
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups


def generate_srt(words: Iterable[WordSegment], output_path: Path) -> Path:
    """Write an SRT sidecar grouped into short readable cues."""
    groups = _group_words(list(words))
    lines: List[str] = []
    for idx, group in enumerate(groups, start=1):
        start = group[0].start
        end = max(group[-1].end, start + 0.2)
        text = " ".join(w.word for w in group)
        lines.append(str(idx))
        lines.append(f"{_format_timestamp(start)} --> {_format_timestamp(end)}")
        lines.append(text)
        lines.append("")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.debug("Wrote SRT sidecar (%d cues) to %s", len(groups), output_path)
    return output_path


def generate_vtt(words: Iterable[WordSegment], output_path: Path) -> Path:
    """Write a WebVTT sidecar grouped into short readable cues."""
    groups = _group_words(list(words))
    lines: List[str] = ["WEBVTT", ""]
    for idx, group in enumerate(groups, start=1):
        start = group[0].start
        end = max(group[-1].end, start + 0.2)
        text = " ".join(w.word for w in group)
        lines.append(str(idx))
        lines.append(
            f"{_format_timestamp(start, vtt=True)} --> {_format_timestamp(end, vtt=True)}"
        )
        lines.append(text)
        lines.append("")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.debug("Wrote VTT sidecar (%d cues) to %s", len(groups), output_path)
    return output_path
