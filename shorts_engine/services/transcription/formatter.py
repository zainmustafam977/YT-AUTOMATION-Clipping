"""
Helpers for formatting, grouping, and analyzing word-level transcripts.
"""

from typing import List, Dict, Any, Optional


class WordSegment:
    def __init__(self, word: str, start: float, end: float, probability: float = 1.0):
        self.word = word.strip()
        self.start = float(start)
        self.end = float(end)
        self.probability = float(probability)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word": self.word,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 3),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WordSegment":
        return cls(
            word=data["word"],
            start=data["start"],
            end=data["end"],
            probability=data.get("probability", 1.0),
        )

    def __repr__(self) -> str:
        return f"[{self.start:.2f}s - {self.end:.2f}s] {self.word}"


class FormattedTranscript:
    def __init__(self, words: List[WordSegment], language: str = "en", full_text: Optional[str] = None):
        self.words = words
        self.language = language
        self._full_text = full_text

    @property
    def full_text(self) -> str:
        if self._full_text:
            return self._full_text
        return " ".join(w.word for w in self.words)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "full_text": self.full_text,
            "words": [w.to_dict() for w in self.words],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FormattedTranscript":
        words = [WordSegment.from_dict(w) for w in data.get("words", [])]
        return cls(
            words=words,
            language=data.get("language", "en"),
            full_text=data.get("full_text"),
        )

    def find_silences(self, min_silence_sec: float = 0.3) -> List[Dict[str, float]]:
        """Identifies natural pauses / silence gaps between words."""
        silences = []
        for i in range(len(self.words) - 1):
            curr_end = self.words[i].end
            next_start = self.words[i + 1].start
            gap = next_start - curr_end
            if gap >= min_silence_sec:
                silences.append({
                    "start": curr_end,
                    "end": next_start,
                    "duration": gap,
                    "after_word": self.words[i].word,
                    "before_word": self.words[i + 1].word,
                })
        return silences

    def get_segment_text(self, start_ms: int, end_ms: int) -> str:
        """Returns the spoken transcript text within a start/end millisecond window."""
        start_sec = start_ms / 1000.0
        end_sec = end_ms / 1000.0
        words = [w.word for w in self.words if (w.start >= start_sec - 0.5 and w.end <= end_sec + 0.5)]
        return " ".join(words)
