"""Session data model (spec §7) and JSON project-file persistence."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_FORMAT_VERSION = 1
_TOKEN_RE = re.compile(r"\S+")


def _json_default(o):
    """numpy scalars (np.bool_, np.float32, ...) -> plain Python values."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


@dataclass
class Word:
    text: str
    start_time: float
    end_time: float
    probability: float


@dataclass
class Sentence:
    index: int
    text: str
    low_confidence: bool = False
    words: list[Word] = field(default_factory=list)
    # Time range used for playback highlighting. Taken from the words at
    # transcription time and kept even if the text is edited afterwards.
    start_time: float = 0.0
    end_time: float = 0.0

    def token_times(self, text: str | None = None) -> list[tuple[int, int, float, float]]:
        """(char start, char end, start time, end time) per whitespace token of the text.

        Uses the word timestamps while the text has the same number of tokens
        as there are words; after edits that change the word count, spreads
        the sentence's time range across the characters instead.
        """
        text = self.text if text is None else text
        tokens = [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
        if self.words and len(self.words) == len(tokens):
            return [(a, b, w.start_time, w.end_time) for (a, b), w in zip(tokens, self.words)]
        t0, t1, n = self.start_time, self.end_time, max(len(text), 1)
        return [(a, b, t0 + (t1 - t0) * a / n, t0 + (t1 - t0) * b / n) for a, b in tokens]

    def mean_probability(self) -> float | None:
        if not self.words:
            return None
        return sum(w.probability for w in self.words) / len(self.words)


@dataclass
class Paragraph:
    index: int
    speaker_id: int | None  # 1-based "Participant N"; None for non-speech
    is_nonspeech: bool
    start_time: float
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def end_time(self) -> float:
        return max((s.end_time for s in self.sentences), default=self.start_time)


@dataclass
class Session:
    audio_path: str
    model_size: str
    engine: str = "faster-whisper"  # named in the export's intro line
    title: str = ""
    boosted_audio_path: str | None = None
    low_confidence_threshold: float = 0.5
    paragraphs: list[Paragraph] = field(default_factory=list)
    # File specs + transcription-run stats for the export header:
    # {"audio": audio.probe() + loudness, "boosted": {...}, "transcription": {...}}
    info: dict = field(default_factory=dict)
    # Optional display names per speaker id ("1" -> "Chris"); JSON keys are strings.
    speaker_names: dict[str, str] = field(default_factory=dict)
    # Speech-cleanup edit plan, applied to the *original* audio by "Render":
    # {"whole": CleanupSettings dict or None,
    #  "blocks": [{"start": s, "end": e, "settings": CleanupSettings dict}, ...]}
    cleanup_plan: dict = field(default_factory=lambda: {"whole": None, "blocks": []})
    # The plan as it was when boosted_audio_path was last rendered (to spot stale renders).
    cleanup_rendered: dict | None = None
    # User-tagged sounds (sneeze, horn, bell...): [{"start", "end", "label"}]. Inserted into the
    # transcript as "(label)" between the adjacent words — now, or when it's (re)transcribed.
    sound_tags: list[dict] = field(default_factory=list)

    def speaker_label(self, speaker_id: int) -> str:
        """The participant's name if one was given, else "Participant N"."""
        return (self.speaker_names.get(str(speaker_id)) or "").strip() or f"Participant {speaker_id}"

    def speaker_ids(self) -> list[int]:
        """Speaker ids actually used by paragraphs, sorted."""
        return sorted({p.speaker_id for p in self.paragraphs if p.speaker_id is not None})

    def renumber(self) -> None:
        """Re-apply 1-based paragraph / sentence indices after structural edits."""
        for pi, para in enumerate(self.paragraphs, start=1):
            para.index = pi
            for si, sent in enumerate(para.sentences, start=1):
                sent.index = si

    def iter_sentences(self):
        for para in self.paragraphs:
            for sent in para.sentences:
                yield para, sent

    # ---- persistence -------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["format_version"] = PROJECT_FORMAT_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        d = dict(d)
        d.pop("format_version", None)
        paragraphs = []
        for p in d.pop("paragraphs", []):
            sentences = []
            for s in p.pop("sentences", []):
                words = [Word(**w) for w in s.pop("words", [])]
                sentences.append(Sentence(words=words, **s))
            paragraphs.append(Paragraph(sentences=sentences, **p))
        return cls(paragraphs=paragraphs, **d)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(self.to_dict(), indent=1, ensure_ascii=False, default=_json_default)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)  # atomic, so a crash mid-save can't destroy edits

    @classmethod
    def load(cls, path: str | Path) -> Session:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
