"""Processing pipeline (spec §5): raw segments -> Session.

Structure, matching samples/multispeaker/nasa-podcast-ep434-2speakers.format-reference.md:
- sentence  = a Whisper segment, further split at sentence-ending punctuation
- paragraph = consecutive sentences by the same speaker without a long pause
- non-speech annotations ("(coughing)", "[laughs]", "*coughing*") become their
  own gray paragraphs and are excluded from diarization.
"""

from __future__ import annotations

import re
import tempfile
import threading
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import audio as audio_mod
from . import diarize as diarize_mod
from .audio import Cancelled
from .backends import Backend, RawSegment, TranscribeOptions, best_backend
from .logs import log
from .model import Paragraph, Sentence, Session, Word
from .progress import ProgressEvent, Reporter

STEPS = ["Preparing audio", "Loading Whisper model", "Transcribing", "Loading speaker model",
         "Separating speakers", "Building transcript"]

# Annotation tokens Whisper emits for non-verbal sounds.
_OPEN, _CLOSE = "([*", ")]*"
# whisper.cpp emits these for silence; they're not sound events, just drop them.
_DROP_RE = re.compile(r"^[\[(]\s*(BLANK_AUDIO|silence|no speech)\s*[\])]$", re.I)
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "vs.", "etc.", "e.g.", "i.e.", "a.m.", "p.m."}

PARAGRAPH_GAP = 2.0  # seconds of silence that starts a new paragraph
PARAGRAPH_MAX = 30.0  # longest paragraph before forcing a break at a sentence end


@dataclass
class PipelineOptions:
    model_size: str
    backend: Backend | None = None  # None = best available
    num_speakers: int | None = None  # None = auto
    max_speakers: int = 4
    low_confidence_threshold: float = 0.5
    vad_filter: bool | None = None
    language: str | None = None
    speaker_model: str | None = None  # "ecapa" / "resemblyzer"; None = default
    boost_description: str | None = None  # filter chain of the boosted copy, for the export header


@dataclass
class _Unit:
    """A sentence-sized piece of a segment, before paragraph grouping."""
    text: str
    words: list[Word]
    start: float
    end: float
    nonspeech: bool
    speaker: int | None = None
    low_confidence: bool = False


def normalize_annotation(text: str) -> str:
    """'*coughing*' -> '(coughing)'; bracketed/parenthesised text kept as Whisper wrote it."""
    t = text.strip()
    if t.startswith("*") and t.endswith("*"):
        return f"({t.strip('*').strip()})"
    return t


def _is_annotation_start(w: str) -> bool:
    return bool(w) and w[0] in _OPEN


def _closes(open_ch: str, w: str) -> bool:
    return w.rstrip(".,!?;:")[-1:] == _CLOSE[_OPEN.index(open_ch)]


def _ends_sentence(w: str) -> bool:
    t = w.rstrip("\"')]")
    if not t or t.lower() in _ABBREVIATIONS or t.endswith(".."):
        return False  # keep trailing-off "..." inside the sentence
    return t[-1] in ".?!"


def split_segment(seg: RawSegment) -> list[_Unit]:
    """Split one segment into sentence units and non-speech annotation units."""
    words = seg.words or [Word(seg.text, seg.start, seg.end, 1.0)]
    units: list[_Unit] = []
    cur: list[Word] = []

    def flush(nonspeech=False):
        nonlocal cur
        if cur:
            text = " ".join(w.text for w in cur).strip()
            if nonspeech:
                text = normalize_annotation(text)
            if text and not _DROP_RE.match(text):
                units.append(_Unit(text, cur, cur[0].start_time, cur[-1].end_time, nonspeech))
        cur = []

    i = 0
    while i < len(words):
        w = words[i]
        if _is_annotation_start(w.text):
            # Collect the whole annotation, e.g. "(door", "closes)".
            flush()
            open_ch, j = w.text[0], i
            while j < len(words) - 1 and not _closes(open_ch, words[j].text):
                j += 1
            if _closes(open_ch, words[j].text):
                cur = words[i:j + 1]
                flush(nonspeech=True)
                i = j + 1
                continue
        cur.append(w)
        if _ends_sentence(w.text):
            flush()
        i += 1
    flush()
    return units


def _group_paragraphs(units: list[_Unit]) -> list[Paragraph]:
    paras: list[Paragraph] = []
    prev: _Unit | None = None
    for u in units:
        para = paras[-1] if paras else None
        new = (
            para is None
            or u.nonspeech != para.is_nonspeech
            or u.speaker != para.speaker_id
            or u.start - prev.end >= PARAGRAPH_GAP
            # Long monologue: break at a sentence end once the paragraph is long.
            or (u.end - para.start_time > PARAGRAPH_MAX and _ends_sentence(prev.text.split()[-1]))
        )
        if new:
            para = Paragraph(index=0, speaker_id=u.speaker, is_nonspeech=u.nonspeech, start_time=u.start)
            paras.append(para)
        para.sentences.append(Sentence(index=0, text=u.text, low_confidence=u.low_confidence,
                                       words=u.words, start_time=u.start, end_time=u.end))
        prev = u
    return paras


def build_session(segments: list[RawSegment], pcm, opts: PipelineOptions, audio_path: str,
                  engine: str, reporter: Reporter | None = None) -> Session:
    def step(name):
        if reporter:
            reporter.step(name)

    units = [u for seg in segments for u in split_segment(seg)]

    # Low-confidence: mean word probability below threshold (spec §5.5).
    for u in units:
        if not u.nonspeech and u.words:
            u.low_confidence = bool(sum(w.probability for w in u.words) / len(u.words) < opts.low_confidence_threshold)

    # Diarization over speech units only (spec §5.6).
    step("Loading speaker model")
    encoder, note = diarize_mod.prepare_encoder(opts.speaker_model, reporter or (lambda *a, **k: None))
    step("Separating speakers")
    speech = [u for u in units if not u.nonspeech]
    result = diarize_mod.diarize(pcm, [(u.start, u.end) for u in speech], opts.num_speakers, opts.max_speakers,
                                 encoder=encoder, fallback_note=note,
                                 progress=reporter or (lambda *a, **k: None))
    for u, label in zip(speech, result.labels):
        u.speaker = label

    step("Building transcript")
    session = Session(audio_path=str(audio_path), model_size=opts.model_size, engine=engine,
                      low_confidence_threshold=opts.low_confidence_threshold)
    session.paragraphs = _group_paragraphs(units)
    session.renumber()
    session.diarization_detail = result.detail  # informational, not persisted
    sentences = sum(len(p.sentences) for p in session.paragraphs)
    log.info("Transcript: %d paragraphs, %d sentences, %d low-confidence, %d non-speech, speakers %s",
             len(session.paragraphs), sentences,
             sum(s.low_confidence for _, s in session.iter_sentences()),
             sum(p.is_nonspeech for p in session.paragraphs), session.speaker_ids())
    return session


def run(audio_path: str | Path, opts: PipelineOptions,
        on_event: Callable[[ProgressEvent], None] | None = None,
        cancel: threading.Event | None = None, boosted_audio_path: str | Path | None = None) -> Session:
    """Full pipeline. Transcribes `boosted_audio_path` if given, else the FLAC playback copy."""
    cancel = cancel or threading.Event()
    rep = Reporter(on_event or (lambda e: None), STEPS)
    backend = opts.backend or best_backend()
    log.info("=== Transcribe %s", audio_path)
    log.info("Options: engine=%s model=%s speakers=%s speaker_model=%s threshold=%.2f boosted=%s",
             backend.label, opts.model_size, opts.num_speakers or "auto",
             opts.speaker_model or diarize_mod.DEFAULT_ENCODER, opts.low_confidence_threshold,
             boosted_audio_path or "no")

    def checkpoint():
        if cancel.is_set():
            raise Cancelled()

    started = datetime.now().astimezone()
    try:
        rep.step("Preparing audio")
        info: dict = {"audio": audio_mod.probe(audio_path)}
        rep(None, "Measuring loudness of the original…")
        info["audio"]["loudness"] = audio_mod.loudness_stats(audio_path)
        if boosted_audio_path:
            info["boosted"] = {"file": Path(boosted_audio_path).name, "filter": opts.boost_description,
                               "loudness": audio_mod.loudness_stats(boosted_audio_path)}
        log.info("File: %s", info)
        source = Path(boosted_audio_path or audio_mod.make_playback_copy(audio_path, rep, cancel))
        with tempfile.TemporaryDirectory(prefix="ts-") as tmp:
            rep(None, f"Decoding {source.name} to 16 kHz for the model…")
            wav = audio_mod.decode_to_wav(source, Path(tmp) / "input16k.wav")
            pcm = audio_mod.load_pcm(wav)
            duration = len(pcm) / audio_mod.SAMPLE_RATE
            log.info("Audio: %s, %.1fs", source, duration)
            checkpoint()
            rep.step("Loading Whisper model")
            backend.prepare(opts.model_size, rep, cancel)
            checkpoint()
            rep.step("Transcribing")
            segments = backend.transcribe(
                wav, pcm, duration,
                TranscribeOptions(opts.model_size, language=opts.language, vad_filter=opts.vad_filter),
                lambda f=None, d="", **i: rep(f, d, audio_total=duration, **i), cancel)
        checkpoint()
        session = build_session(segments, pcm, opts, str(Path(audio_path).resolve()), backend.engine_name, rep)
        session.boosted_audio_path = str(Path(boosted_audio_path).resolve()) if boosted_audio_path else None
        session.title = Path(audio_path).stem
        total = rep.finish()
        info["transcription"] = {
            "engine": backend.label, "model": opts.model_size,
            "started": started.isoformat(timespec="seconds"),
            "processing_seconds": round(total, 1), "audio_seconds": round(duration, 1),
            "speaker_model": session.diarization_detail.split(":", 1)[0],
            "speakers_requested": opts.num_speakers, "speakers_found": len(session.speaker_ids()),
            "diarization_detail": session.diarization_detail,
            "low_confidence_threshold": opts.low_confidence_threshold,
            "source": "boosted copy" if boosted_audio_path else "original",
        }
        session.info = info
        log.info("Done: %.1fs for %.1fs of audio (%.1fx real time)", total, duration, duration / max(total, 1e-6))
        return session
    except Cancelled:
        log.warning("Cancelled by user during: %s", rep.name or "?")
        raise
    except Exception as e:  # caller logs the traceback
        log.error("Transcription failed during %s: %s: %s", rep.name or "?", type(e).__name__, e)
        raise
