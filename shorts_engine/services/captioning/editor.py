"""Caption editor (E-016).

Per-clip caption word list stored on ClipCandidate.caption_words_json.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from shorts_engine.db.models import ClipCandidate, Transcript


logger = logging.getLogger(__name__)


class CaptionEditError(Exception):
    def __init__(self, message: str, code: str = "CAPTION_EDIT_ERROR") -> None:
        super().__init__(message)
        self.code = code


class CaptionEditor:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_words(self, clip_id: str) -> List[Dict[str, Any]]:
        cand = self._candidate(clip_id)
        if cand.caption_words_json:
            try:
                words = json.loads(cand.caption_words_json)
                if isinstance(words, list) and words:
                    return words
            except Exception:
                pass
        return self._regenerate_from_transcript(clip_id)

    def set_words(
        self, clip_id: str, words: List[Dict[str, Any]], actor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        self._validate(words)
        cand = self._candidate(clip_id)
        cand.caption_words_json = json.dumps(words, sort_keys=True)
        cand.caption_edited_at = datetime.utcnow()
        cand.caption_edited_by = actor
        self.db.commit()
        return words

    def reset_to_transcript(
        self, clip_id: str, actor: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        cand = self._candidate(clip_id)
        cand.caption_words_json = None
        cand.caption_edited_at = None
        cand.caption_edited_by = None
        self.db.commit()
        return self._regenerate_from_transcript(clip_id)

    def _candidate(self, clip_id: str) -> ClipCandidate:
        from shorts_engine.db.models import Clip
        clip = self.db.query(Clip).filter_by(id=clip_id).first()
        if clip is None:
            raise CaptionEditError("clip not found", code="CAPTION_CLIP_NOT_FOUND")
        cand = self.db.query(ClipCandidate).filter_by(id=clip.clip_candidate_id).first()
        if cand is None:
            raise CaptionEditError("no candidate", code="CAPTION_NO_CANDIDATE")
        return cand

    def _regenerate_from_transcript(self, clip_id: str) -> List[Dict[str, Any]]:
        cand = self._candidate(clip_id)
        from shorts_engine.db.models import SourceVideo
        sv = self.db.query(SourceVideo).filter_by(id=cand.source_video_id).first()
        if sv is None:
            return []
        tx = self.db.query(Transcript).filter_by(source_video_id=sv.id).first()
        if tx is None or not tx.word_level_json:
            return []
        try:
            all_words = json.loads(tx.word_level_json)
        except Exception:
            return []
        if not isinstance(all_words, list):
            return []
        out: List[Dict[str, Any]] = []
        for w in all_words:
            try:
                ws = int(w.get("start_ms", 0))
                we = int(w.get("end_ms", 0))
                text = w.get("word", "")
            except Exception:
                continue
            if we < cand.start_ms:
                continue
            if ws > cand.end_ms:
                break
            ws = max(ws, int(cand.start_ms))
            we = min(we, int(cand.end_ms))
            if we <= ws:
                continue
            out.append({"word": text, "start_ms": ws, "end_ms": we})
        return out

    def _validate(self, words: List[Dict[str, Any]]) -> None:
        if not isinstance(words, list):
            raise CaptionEditError("words must be a list", code="CAPTION_BAD_WORDS")
        for i, w in enumerate(words):
            if not isinstance(w, dict):
                raise CaptionEditError(f"word {i} not an object", code="CAPTION_BAD_WORDS")
            for k in ("word", "start_ms", "end_ms"):
                if k not in w:
                    raise CaptionEditError(f"word {i} missing {k}", code="CAPTION_BAD_WORDS")
            if w["end_ms"] < w["start_ms"]:
                raise CaptionEditError(
                    f"word {i} end_ms < start_ms", code="CAPTION_BAD_TIMING"
                )
