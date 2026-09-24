"""
Helpers for formatting, grouping, and analyzing word-level transcripts.
"""

from typing import List, Dict, Any, Optional, Union


class WordSegment:
    def __init__(self, word: str, start: float, end: float, probability: float = 1.0, score: Optional[float] = None):
        self.word = str(word).strip()
        self.start = float(start)
        self.end = float(end)
        self.probability = float(probability)
        self.score = float(score if score is not None else self.probability)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word": self.word,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 3),
            "score": round(self.score, 3),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "WordSegment":
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict or WordSegment, got {type(data).__name__}")
        if "word" not in data or "start" not in data or "end" not in data:
            raise KeyError(f"WordSegment requires 'word', 'start', and 'end' keys, got: {list(data.keys())}")

        try:
            start_val = float(data["start"])
            end_val = float(data["end"])
        except (ValueError, TypeError) as e:
            raise ValueError(f"WordSegment start and end must be numeric: {e}")

        prob = data.get("probability", data.get("score", 1.0))
        score = data.get("score", prob)
        return cls(
            word=data["word"],
            start=start_val,
            end=end_val,
            probability=float(prob),
            score=float(score),
        )

    def __getitem__(self, item: str) -> Any:
        if item == "word":
            return self.word
        elif item == "start":
            return self.start
        elif item == "end":
            return self.end
        elif item == "probability":
            return self.probability
        elif item == "score":
            return self.score
        raise KeyError(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self[item]
        except KeyError:
            return default

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
    def from_dict(cls, data: Any) -> "FormattedTranscript":
        if isinstance(data, list):
            words = [WordSegment.from_dict(w) for w in data]
            return cls(words=words, language="en", full_text=None)
        elif isinstance(data, dict):
            raw_words = data.get("words", [])
            words = [WordSegment.from_dict(w) for w in raw_words]
            return cls(
                words=words,
                language=data.get("language", "en"),
                full_text=data.get("full_text"),
            )
        else:
            raise TypeError(f"FormattedTranscript.from_dict expects dict or list, got {type(data).__name__}")

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
