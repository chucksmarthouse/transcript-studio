"""PDF and Word (.docx) export — the same content and colours as the markdown export.

Colour emoji don't survive PDF/Word rendering reliably, so each emoji marker
(🔵🟢🟠🔴⚫) becomes a "●" in the same colour.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .export import (
    LOW_CONF_COLOR, NONSPEECH_COLOR, details_sections, format_timestamp, speaker_style,
)
from .model import Session
from .sounds import ANNOTATION_RE

DOT = "●"
INTRO = ("Automated transcript ({engine}, model: {model}) with best-effort speaker separation. Speaker labels "
         "and the unintelligible/low-confidence flag are machine-generated — verify against the audio before "
         "relying on them.")
FORMATS = {"md": "Markdown (.md)", "pdf": "PDF (.pdf)", "docx": "Word document (.docx)"}


# ---------------------------------------------------------------- shared layout

@dataclass
class Line:
    number: str  # "3.2"
    kind: str  # speech | low | nonspeech
    color: str
    text: str


@dataclass
class Block:
    heading: str  # "3. Participant 1 — 00:00:16" (without the dot)
    color: str  # dot colour
    label: str | None  # speaker label, or None for non-speech
    timestamp: str
    lines: list[Line] = field(default_factory=list)


def layout(session: Session) -> tuple[list[tuple[str, str]], list[Block]]:
    """(legend entries as (colour, label), paragraph blocks) — the transcript's visible structure."""
    legend = [(speaker_style(sid)[1], session.speaker_label(sid)) for sid in session.speaker_ids()]
    legend += [(LOW_CONF_COLOR, "Low-confidence / possibly unintelligible"),
               (NONSPEECH_COLOR, "Non-speech sound (e.g. coughing) — automatically detected")]
    blocks = []
    for para in session.paragraphs:
        ts = format_timestamp(para.start_time)
        nonspeech = para.is_nonspeech or para.speaker_id is None
        if nonspeech:
            b = Block(f"{para.index}.", NONSPEECH_COLOR, None, ts)
        else:
            color = speaker_style(para.speaker_id)[1]
            b = Block(f"{para.index}.", color, session.speaker_label(para.speaker_id), ts)
        for sent in para.sentences:
            num = f"{para.index}.{sent.index}"
            if nonspeech:
                b.lines.append(Line(num, "nonspeech", NONSPEECH_COLOR, sent.text))
            elif sent.low_confidence:
                b.lines.append(Line(num, "low", LOW_CONF_COLOR, sent.text))
            else:
                b.lines.append(Line(num, "speech", speaker_style(para.speaker_id)[1], sent.text))
        blocks.append(b)
    return legend, blocks


def _segments(text: str) -> list[tuple[str, bool]]:
    """Split text into (piece, is_annotation) so "(sneeze)" etc. can be italic."""
    out, pos = [], 0
    for m in ANNOTATION_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def _code_segments(value: str) -> list[tuple[str, bool]]:
    """Split a details value at `code` spans: (piece, is_code)."""
    parts = value.split("`")
    return [(p, i % 2 == 1) for i, p in enumerate(parts) if p]


def _title(session: Session) -> str:
    return f"{session.title or Path(session.audio_path).stem} Transcript"


def _intro(session: Session) -> str:
    return INTRO.format(engine=session.engine, model=session.model_size)


# ---------------------------------------------------------------- HTML / PDF

def to_html(session: Session, details: bool = True, exported: datetime | None = None) -> str:
    e = lambda t: html.escape(t, quote=False)
    legend, blocks = layout(session)
    h = [f"<h1>{e(_title(session))}</h1>", f"<p>{e(_intro(session))}</p>"]
    if details:
        for title, rows in details_sections(session, exported):
            h.append(f"<p><b>{e(title)}</b></p>")
            h.append('<table border="1" cellspacing="0" cellpadding="4" style="border-color:#cbd5e1">')
            for label, value, sid in rows:
                lab = (f'<span style="color:{speaker_style(sid)[1]}">{DOT}</span> {e(label)}'
                       if sid is not None else e(label))
                val = "".join(f'<span style="font-family:monospace">{e(p)}</span>' if code else e(p)
                              for p, code in _code_segments(value))
                h.append(f"<tr><td><b>{lab}</b></td><td>{val}</td></tr>")
            h.append("</table>")
    h.append("<p><b>Legend:</b></p><p>")
    h.append("<br>".join(f'<span style="color:{c}">{DOT} {e(label)}</span>' for c, label in legend))
    h.append("</p><hr>")
    for b in blocks:
        who = f" {e(b.label)} —" if b.label else ""
        h.append(f'<h3>{e(b.heading)} <span style="color:{b.color}">{DOT}</span>{who} <i>{b.timestamp}</i></h3>')
        lines = []
        for ln in b.lines:
            marker = f'<span style="color:{ln.color}">{DOT}</span> ' if ln.kind in ("low", "nonspeech") else ""
            if ln.kind == "nonspeech":
                body = f"<i>{e(ln.text)}</i>"
            else:
                body = "".join(f"<i>{e(p)}</i>" if ann else e(p) for p, ann in _segments(ln.text))
            lines.append(f'<b>{ln.number}</b> {marker}<span style="color:{ln.color}">{body}</span>')
        h.append("<p>" + "<br>".join(lines) + "</p>")
    return "\n".join(h)


def export_pdf(session: Session, path: str | Path, details: bool = True) -> Path:
    import os

    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPdfWriter, QTextDocument
    from PySide6.QtWidgets import QApplication

    # QTextDocument needs a Qt application (e.g. when called from the CLI). Create a full
    # QApplication, not a bare QGuiApplication, so widgets can still be created afterwards
    # in the same process; render offscreen when there's no display.
    if QGuiApplication.instance() is None:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        export_pdf._app = QApplication([])
    doc = QTextDocument()
    doc.setDefaultStyleSheet("h1 { font-size: 20pt; } h3 { font-size: 12pt; margin-top: 10px; } "
                             "p { margin: 4px 0; }")
    doc.setHtml(to_html(session, details))
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.Letter))
    writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Millimeter)
    writer.setTitle(_title(session))
    writer.setCreator("Transcript Studio")
    doc.print_(writer)  # paginates and adds page numbers
    return Path(path)


# ---------------------------------------------------------------- Word

def export_docx(session: Session, path: str | Path, details: bool = True) -> Path:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt, RGBColor

    rgb = lambda c: RGBColor.from_string(c.lstrip("#").upper())
    doc = Document()
    doc.core_properties.title = _title(session)
    doc.core_properties.author = "Transcript Studio"
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    def run(par, text, color=None, bold=False, italic=False, mono=False):
        r = par.add_run(text)
        r.bold, r.italic = bold, italic
        if color:
            r.font.color.rgb = rgb(color)
        if mono:
            r.font.name = "Courier New"
        return r

    legend, blocks = layout(session)
    doc.add_heading(_title(session), level=1)
    doc.add_paragraph(_intro(session))
    if details:
        for title, rows in details_sections(session):
            run(doc.add_paragraph(), title, bold=True)
            table = doc.add_table(rows=0, cols=2)
            table.style = "Table Grid"
            for label, value, sid in rows:
                cells = table.add_row().cells
                lp = cells[0].paragraphs[0]
                if sid is not None:
                    run(lp, DOT + " ", color=speaker_style(sid)[1])
                run(lp, label, bold=True)
                vp = cells[1].paragraphs[0]
                for piece, code in _code_segments(value):
                    run(vp, piece, mono=code)
            doc.add_paragraph()
    run(doc.add_paragraph(), "Legend:", bold=True)
    for color, label in legend:  # the coloured dot is the bullet
        lp = doc.add_paragraph()
        lp.paragraph_format.space_after = Pt(0)
        run(lp, f"{DOT} {label}", color=color)

    for b in blocks:
        hp = doc.add_heading(level=3)
        run(hp, b.heading + " ")
        run(hp, DOT + " ", color=b.color)
        if b.label:
            run(hp, f"{b.label} — ")
        run(hp, b.timestamp, italic=True)
        par = doc.add_paragraph()
        for i, ln in enumerate(b.lines):
            if i:
                par.runs[-1].add_break(WD_BREAK.LINE)
            run(par, ln.number + " ", bold=True)
            if ln.kind in ("low", "nonspeech"):
                run(par, DOT + " ", color=ln.color)
            if ln.kind == "nonspeech":
                run(par, ln.text, color=ln.color, italic=True)
            else:
                for piece, ann in _segments(ln.text):
                    run(par, piece, color=ln.color, italic=ann)
    doc.save(str(path))
    return Path(path)


def export(session: Session, path: str | Path, fmt: str | None = None, details: bool = True) -> Path:
    """Export in the format given (md / pdf / docx) or implied by the file extension."""
    from .export import export_markdown

    fmt = (fmt or Path(path).suffix.lstrip(".")).lower()
    session.renumber()
    if fmt == "pdf":
        return export_pdf(session, path, details)
    if fmt == "docx":
        return export_docx(session, path, details)
    if fmt == "md":
        export_markdown(session, path, details)
        return Path(path)
    raise ValueError(f"Unknown export format: {fmt!r} (use md, pdf or docx)")
