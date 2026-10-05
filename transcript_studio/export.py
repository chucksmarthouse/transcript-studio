"""Markdown export in the legacy format (spec §8, samples/multispeaker/nasa-podcast-ep434-2speakers.format-reference.md)."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .model import Session
from .sounds import italicize_annotations

# (emoji, color) per speaker, in Participant order. The first three are fixed
# by the legacy format; the rest extend it for sessions with more speakers.
SPEAKER_PALETTE = [
    ("🔵", "#1d4ed8"),
    ("🟢", "#059669"),
    ("🟠", "#d97706"),
    ("🟣", "#7c3aed"),
    ("🟤", "#92400e"),
    ("🟡", "#ca8a04"),
]
LOW_CONF_EMOJI, LOW_CONF_COLOR = "🔴", "#dc2626"
NONSPEECH_EMOJI, NONSPEECH_COLOR = "⚫", "#6b7280"


def speaker_style(speaker_id: int) -> tuple[str, str]:
    """(emoji, color) for a 1-based speaker id."""
    return SPEAKER_PALETTE[(speaker_id - 1) % len(SPEAKER_PALETTE)]


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def span(color: str, text: str) -> str:
    return f'<span style="color:{color}">{text}</span>'


def _fmt_dt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value  # e.g. a bare year from an ID3 tag
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # show everything in local time, e.g. "MDT"
    return dt.strftime("%Y-%m-%d %H:%M %Z").strip()


Row = tuple[str, str, int | None]  # (label, value with `code` spans, speaker id for participant rows)


def details_sections(session: Session, exported: datetime | None = None) -> list[tuple[str, list[Row]]]:
    """'File details' + 'Transcription details' tables for the export header, as data.

    Values may contain `backtick` code spans; each output format renders them its own way.
    """
    info = session.info or {}
    a = info.get("audio", {})
    t = info.get("transcription", {})
    rows: list[Row] = []

    def _row(label, value, sid=None):
        rows.append((label, str(value), sid))

    sections = [("File details", rows)]
    _row("File", f"`{a.get('file') or Path(session.audio_path).name}`")
    if a.get("codec_name") or a.get("codec"):
        # Short ffmpeg name, upper-cased ("aac" -> "AAC"); older projects only have the long name.
        codec = a["codec_name"].upper().replace("_", " ") if a.get("codec_name") else a["codec"]
        if a.get("codec_profile"):
            codec += f" ({a['codec_profile']})"
        suffix = Path(a.get("file") or session.audio_path).suffix.lower()
        _row("Format", f"{codec}, {suffix} file" if suffix else codec)
    audio_bits = []
    if a.get("sample_rate"):
        audio_bits.append(f"{a['sample_rate'] / 1000:g} kHz")
    if a.get("channels"):
        audio_bits.append({1: "mono", 2: "stereo"}.get(a["channels"], f"{a['channels']} channels"))
    if a.get("bit_rate"):
        audio_bits.append(f"{a['bit_rate'] / 1000:.0f} kb/s")
    if audio_bits:
        _row("Audio", ", ".join(audio_bits))
    if a.get("duration"):
        _row("Duration", format_timestamp(a["duration"]))
    if a.get("size_bytes"):
        _row("Size", f"{a['size_bytes'] / 2**20:.1f} MB")
    if a.get("created") or a.get("device"):
        rec = " · ".join(x for x in (_fmt_dt(a.get("created")), a.get("device")) if x)
        _row("Recorded", rec)
    if a.get("loudness"):
        ld = a["loudness"]
        _row("Loudness", f"{ld.get('integrated_lufs')} LUFS integrated, "
                                      f"{ld.get('true_peak_dbtp')} dBFS peak")
    b = info.get("boosted")
    if b:
        text = f"`{b.get('file')}`"
        if b.get("filter"):
            text += f" — `{b['filter']}`"
        if b.get("loudness"):
            text += f" — {b['loudness'].get('integrated_lufs')} LUFS"
        _row("Processed copy", text)

    # Content stats from the current (possibly edited) transcript.
    speech = [(p, s) for p, s in session.iter_sentences() if not p.is_nonspeech and p.speaker_id is not None]
    nonspeech = sum(len(p.sentences) for p in session.paragraphs if p.is_nonspeech)
    words = sum(len(s.text.split()) for _, s in speech)
    low = sum(s.low_confidence for _, s in speech)
    probs = [w.probability for _, s in speech for w in s.words]
    rows = []
    sections.append(("Transcription details", rows))
    engine = t.get("engine") or session.engine
    _row("Engine", f"{engine}, model {session.model_size}")
    if t.get("started"):
        run = _fmt_dt(t["started"])
        if t.get("processing_seconds"):
            run += f" · took {format_timestamp(t['processing_seconds'])}"
            if t.get("audio_seconds"):
                run += f" ({t['audio_seconds'] / max(t['processing_seconds'], 1e-6):.1f}× real time)"
        _row("Transcribed", run)
    if t.get("source"):
        _row("Transcribed from", t["source"])
    if t.get("speaker_model"):
        req = t.get("speakers_requested")
        _row("Speaker separation", f"{t['speaker_model']}, "
                                                f"{'set to ' + str(req) if req else 'auto'} → "
                                                f"{t.get('speakers_found')} found")
    _row("Content", f"{len(session.paragraphs)} paragraphs · "
                                 f"{sum(len(p.sentences) for p in session.paragraphs)} sentences · "
                                 f"{words:,} words")
    conf = f"{low} of {len(speech)} sentences ({low / max(len(speech), 1):.0%}) below " \
           f"{session.low_confidence_threshold:.2f}"
    if probs:
        conf += f" · mean word confidence {sum(probs) / len(probs):.2f}"
    _row("Low confidence", conf)
    _row("Non-speech events", nonspeech)
    talk = {}
    for p, s in speech:
        d, w = talk.get(p.speaker_id, (0.0, 0))
        talk[p.speaker_id] = (d + max(0.0, s.end_time - s.start_time), w + len(s.text.split()))
    total_talk = sum(d for d, _ in talk.values())
    for sid in sorted(talk):
        d, w = talk[sid]
        share = f" ({d / total_talk:.0%})" if total_talk else ""
        _row(session.speaker_label(sid), f"{format_timestamp(d)} talk time{share} · {w:,} words", sid)
    _row("Exported", (exported or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M %Z"))
    return sections


def details_block(session: Session, exported: datetime | None = None) -> list[str]:
    """The details tables as markdown lines."""
    lines: list[str] = []
    for i, (title, rows) in enumerate(details_sections(session, exported)):
        lines += ([""] if i else []) + [f"**{title}**", "", "| | |", "|---|---|"]
        for label, value, sid in rows:
            label = f"{speaker_style(sid)[0]} {label}" if sid is not None else label
            lines.append(f"| {label} | {value} |")
    return lines


def to_markdown(session: Session, details: bool = True) -> str:
    """The §8 legacy format; `details` adds the file/transcription tables after the intro."""
    title = session.title or Path(session.audio_path).stem
    lines = [
        f"# {title} Transcript",
        "",
        f"Automated transcript ({session.engine}, model: {session.model_size}) with best-effort "
        "speaker separation. Speaker labels and the unintelligible/low-confidence flag are "
        "machine-generated — verify against the audio before relying on them.",
        "",
        *([*details_block(session), ""] if details else []),
        "**Legend:**",
        "",
    ]
    for sid in session.speaker_ids():
        emoji, color = speaker_style(sid)
        lines.append(f"- {emoji} {span(color, html.escape(session.speaker_label(sid), quote=False))}")
    lines.append(f"- {LOW_CONF_EMOJI} {span(LOW_CONF_COLOR, 'Low-confidence / possibly unintelligible')}")
    lines.append(
        f"- {NONSPEECH_EMOJI} "
        f"{span(NONSPEECH_COLOR, 'Non-speech sound (e.g. coughing) — automatically detected')}"
    )
    lines += ["", "---"]

    for para in session.paragraphs:
        lines.append("")
        ts = format_timestamp(para.start_time)
        if para.is_nonspeech or para.speaker_id is None:
            lines.append(f"### {para.index}. {NONSPEECH_EMOJI} _{ts}_")
        else:
            emoji, _ = speaker_style(para.speaker_id)
            lines.append(f"### {para.index}. {emoji} {session.speaker_label(para.speaker_id)} — _{ts}_")
        lines.append("")

        body = []
        for sent in para.sentences:
            num = f"**{para.index}.{sent.index}**"
            if para.is_nonspeech:
                body.append(f"{num} {NONSPEECH_EMOJI} {span(NONSPEECH_COLOR, f'_{sent.text}_')}")
            elif sent.low_confidence:
                body.append(f"{num} {LOW_CONF_EMOJI} {span(LOW_CONF_COLOR, italicize_annotations(sent.text))}")
            else:
                _, color = speaker_style(para.speaker_id)
                body.append(f"{num} {span(color, italicize_annotations(sent.text))}")
        # Markdown hard line break: two trailing spaces on all but the last line.
        lines += [b + "  " for b in body[:-1]] + body[-1:]

    return "\n".join(lines) + "\n"


def export_markdown(session: Session, path: str | Path, details: bool = True) -> None:
    Path(path).write_text(to_markdown(session, details), encoding="utf-8")
