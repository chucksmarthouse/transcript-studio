"""Zoomable, color-coded audio envelope with range selection (playback panel).

- Mouse wheel zooms around the cursor; Shift+wheel (or a horizontal wheel)
  scrolls; the scrollbar and −/+/Fit buttons do the same.
- While playing, the view follows the playhead (when follow is on).
- Drag to select a range: playback then starts at its start and stops (or
  loops) at its end. Drag either edge of a selection to adjust it. A click
  without dragging, Esc or "Clear" removes it.
- Cleanup-plan blocks show as numbered bands in a strip under the ruler;
  clicking one emits ``blockClicked`` so it can be edited.
- Optional words lane under the waveform: when zoomed in (≤ WORDS_MAX_SPAN
  visible), each transcript word is drawn under its own audio, in its
  speaker color, and scrolls with playback. User sound tags show there too
  (purple, italic); right-click one to remove it.
- After transcription, bars are colored like the transcript text: speaker
  color, red for low-confidence, gray for non-speech; untranscribed audio is
  neutral.
"""

from __future__ import annotations

import bisect
import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollBar, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..envelope import ENV_RATE
from ..export import LOW_CONF_COLOR, NONSPEECH_COLOR, speaker_style
from ..model import Session
from ..sounds import ANNOTATION_RE

NEUTRAL_COLOR = "#94a3b8"
RULER_H = 16
BLOCK_H = 14  # cleanup-plan strip under the ruler
EDGE_GRAB_PX = 6
WORD_H = 20  # words lane under the waveform
WORDS_MAX_SPAN = 40.0  # seconds visible or less before words are drawn
TAG_COLOR = "#7c3aed"
MIN_SPAN = 1.0  # seconds visible at maximum zoom
_TICKS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]


def fmt_time(t: float, precise: bool = False) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    sec = f"{s:04.1f}" if precise else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{sec}" if h else f"{int(m):02d}:{sec}"


def selection_action(pos: float, last_pos: float | None, sel: tuple[float, float] | None,
                     loop: bool) -> str | None:
    """What to do when playback crosses the selection end: 'loop', 'stop' or None."""
    if sel is None or last_pos is None:
        return None
    _, end = sel
    if last_pos < end <= pos and pos - last_pos < 2.0:  # a real crossing, not a user jump
        return "loop" if loop else "stop"
    return None


class WaveformView(QWidget):
    selectionChanged = Signal(object)  # (start, end) or None
    viewChanged = Signal()
    blockClicked = Signal(int)  # index into the blocks given to set_blocks()
    tagRemoveRequested = Signal(int)  # index into session.sound_tags

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)
        self.disp = np.zeros(0, dtype=np.float32)  # display-scaled envelope 0..1
        self.cidx = np.zeros(0, dtype=np.uint8)  # color index per envelope value
        self.palette = [QColor(NEUTRAL_COLOR)]
        self.duration = 0.0
        self.view_start, self.view_span = 0.0, 1.0
        self.playhead = 0.0
        self.selection: tuple[float, float] | None = None
        self._drag: tuple[float, float] | None = None  # (press x, press time)
        self._edge: int | None = None  # 0/1 while dragging the selection's start/end edge
        self._hover_t: float | None = None
        self.blocks: list[tuple[float, float]] = []
        self.active_block: int | None = None
        self.show_words = False
        self.words: list[tuple[float, float, str, str, bool]] = []  # (t0, t1, text, color, italic)
        self._word_starts: list[float] = []
        self.tags: list[tuple[float, float, str]] = []

    # ---- data

    def set_envelope(self, env: np.ndarray | None):
        keep_view = env is not None and abs(len(env) / ENV_RATE - self.duration) < 0.5
        if env is None or not len(env):
            self.disp, self.duration = np.zeros(0, dtype=np.float32), 0.0
        else:
            voiced = env[env > 1e-4]
            norm = float(np.percentile(voiced, 99.9)) if len(voiced) else 1.0
            # Normalise to the loud parts, then a gamma lift so quiet speech stays visible.
            self.disp = np.clip(env / max(norm, 1e-4), 0, 1) ** 0.8
            self.duration = len(env) / ENV_RATE
        if len(self.cidx) != len(self.disp):
            self.cidx = np.zeros(len(self.disp), dtype=np.uint8)
        if not keep_view:
            self.view_start, self.view_span = 0.0, max(self.duration, MIN_SPAN)
        self.viewChanged.emit()
        self.update()

    def set_session(self, session: Session | None):
        """Color the envelope like the transcript text."""
        palette = [QColor(NEUTRAL_COLOR), QColor(NONSPEECH_COLOR), QColor(LOW_CONF_COLOR)]
        cidx = np.zeros(len(self.disp), dtype=np.uint8)
        if session is not None and len(cidx):
            speaker_index: dict[int, int] = {}
            for para in session.paragraphs:
                for sent in para.sentences:
                    if para.is_nonspeech or para.speaker_id is None:
                        k = 1
                    elif sent.low_confidence:
                        k = 2
                    else:
                        if para.speaker_id not in speaker_index:
                            speaker_index[para.speaker_id] = len(palette)
                            palette.append(QColor(speaker_style(para.speaker_id)[1]))
                        k = speaker_index[para.speaker_id]
                    a = max(0, int(sent.start_time * ENV_RATE))
                    b = min(len(cidx), max(a + 1, int(math.ceil(sent.end_time * ENV_RATE))))
                    cidx[a:b] = k
        self.palette, self.cidx = palette, cidx
        self._set_words(session)
        self.update()

    def _set_words(self, session: Session | None):
        words = []
        for para in (session.paragraphs if session else []):
            for sent in para.sentences:
                if para.is_nonspeech or para.speaker_id is None:
                    color = NONSPEECH_COLOR
                elif sent.low_confidence:
                    color = LOW_CONF_COLOR
                else:
                    color = speaker_style(para.speaker_id)[1]
                for a, b, t0, t1 in sent.token_times():
                    token = sent.text[a:b]
                    words.append((t0, t1, token, color, para.is_nonspeech or bool(ANNOTATION_RE.search(token))))
        words.sort(key=lambda w: w[0])
        self.words, self._word_starts = words, [w[0] for w in words]
        self.tags = [(float(t["start"]), float(t["end"]), t["label"]) for t in (session.sound_tags if session else [])]

    def set_show_words(self, on: bool):
        self.show_words = on
        self.setMinimumHeight(115 if on else 90)
        self.update()

    def visible_words(self) -> list[tuple[float, float, str, str, bool]]:
        """Words to draw in the lane (none when zoomed out too far to read them)."""
        if not self.show_words or self.view_span > WORDS_MAX_SPAN:
            return []
        lo = max(0, bisect.bisect_left(self._word_starts, self.view_start) - 1)
        hi = bisect.bisect_right(self._word_starts, self.view_start + self.view_span)
        return self.words[lo:hi]

    def _tag_at(self, x: float) -> int | None:
        t = self.time_at(x)
        pad = 4 * self.view_span / max(self.width(), 1)  # a few px of slack for short tags
        return next((i for i, (a, b, _) in enumerate(self.tags) if a - pad <= t <= b + pad), None)

    # ---- view

    def time_at(self, x: float) -> float:
        return self.view_start + x / max(self.width(), 1) * self.view_span

    def x_at(self, t: float) -> float:
        return (t - self.view_start) / self.view_span * self.width()

    def set_view(self, start: float, span: float | None = None):
        if span is not None:
            self.view_span = min(max(span, MIN_SPAN), max(self.duration, MIN_SPAN))
        self.view_start = min(max(0.0, start), max(0.0, self.duration - self.view_span))
        self.viewChanged.emit()
        self.update()

    def zoom(self, factor: float, anchor: float | None = None):
        anchor = self.view_start + self.view_span / 2 if anchor is None else anchor
        frac = (anchor - self.view_start) / self.view_span
        span = min(max(self.view_span / factor, MIN_SPAN), max(self.duration, MIN_SPAN))
        self.set_view(anchor - frac * span, span)

    def frame(self, start: float, end: float, fill: float = 2 / 3):
        """Zoom and center so [start, end] fills `fill` of the width (limited by max zoom)."""
        span = (end - start) / fill
        self.set_view((start + end) / 2 - max(span, MIN_SPAN) / 2, span)

    def center_on(self, t: float):
        self.set_view(t - self.view_span / 2)

    def fit(self):
        self.set_view(0.0, max(self.duration, MIN_SPAN))

    def set_playhead(self, t: float, follow: bool):
        if t == self.playhead and not follow:
            return  # paused: nothing to redraw
        self.playhead = t
        if follow and self._drag is None and self.duration:
            if t < self.view_start or t > self.view_start + 0.92 * self.view_span:
                self.set_view(t - 0.08 * self.view_span)
                return
        self.update()

    def set_blocks(self, blocks: list[tuple[float, float]], active: int | None = None):
        self.blocks, self.active_block = list(blocks), active
        self.update()

    def block_at(self, x: float) -> int | None:
        t = self.time_at(x)
        return next((i for i, (a, b) in enumerate(self.blocks) if a <= t <= b), None)

    def _edge_at(self, x: float) -> int | None:
        if not self.selection:
            return None
        for i, t in enumerate(self.selection):
            if abs(self.x_at(t) - x) <= EDGE_GRAB_PX:
                return i
        return None

    def set_selection(self, sel: tuple[float, float] | None, emit=True):
        self.selection = sel
        self.update()
        if emit:
            self.selectionChanged.emit(sel)

    # ---- input

    def wheelEvent(self, e):
        dy, dx = e.angleDelta().y(), e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier or abs(dx) > abs(dy):
            steps = (dx or dy) / 120
            self.set_view(self.view_start - steps * 0.15 * self.view_span)
        elif dy:
            self.zoom(1.25 ** (dy / 120), self.time_at(e.position().x()))
        e.accept()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.duration:
            return
        x, y = e.position().x(), e.position().y()
        if RULER_H <= y < RULER_H + BLOCK_H:  # the block strip
            i = self.block_at(x)
            if i is not None:
                self.blockClicked.emit(i)
            return
        self._edge = self._edge_at(x)
        if self._edge is None:
            self._drag = (x, self.time_at(x))

    def mouseMoveEvent(self, e):
        x = e.position().x()
        self._hover_t = self.time_at(x)
        if self._edge is not None:
            t = min(max(0.0, self.time_at(x)), self.duration)
            sel = list(self.selection)
            sel[self._edge] = t
            if sel[0] > sel[1]:  # dragged one edge past the other
                sel.reverse()
                self._edge = 1 - self._edge
            self.selection = (sel[0], sel[1])
        elif self._drag is not None and abs(x - self._drag[0]) > 3:
            a, b = sorted((self._drag[1], self.time_at(x)))
            self.selection = (max(0.0, a), min(self.duration, b))
        elif e.buttons() == Qt.NoButton:
            near_edge = self._edge_at(x) is not None and e.position().y() >= RULER_H + BLOCK_H
            self.setCursor(Qt.SizeHorCursor if near_edge else Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._edge is not None:
            self._edge = None
            self.set_selection(self.selection)
            return
        if self._drag is None:
            return
        dragged = abs(e.position().x() - self._drag[0]) > 3
        self._drag = None
        self.set_selection(self.selection if dragged else None)

    def leaveEvent(self, e):
        self._hover_t = None
        self.update()

    def contextMenuEvent(self, e):
        if self.show_words and e.pos().y() >= self.height() - WORD_H:
            i = self._tag_at(e.pos().x())
            if i is not None:
                menu = QMenu(self)
                act = menu.addAction(f"Remove sound tag ({self.tags[i][2]})")
                act.triggered.connect(lambda: self.tagRemoveRequested.emit(i))
                menu.exec(e.globalPos())
                return
        super().contextMenuEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.selection:
            self.set_selection(None)
        else:
            super().keyPressEvent(e)

    # ---- painting

    def paintEvent(self, _e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#f8fafc"))
        p.fillRect(QRectF(0, 0, w, RULER_H), QColor("#e2e8f0"))
        if not self.duration:
            p.setPen(QColor("#6b7280"))
            p.drawText(self.rect(), Qt.AlignCenter, "Open an audio file to see its waveform")
            return
        lane_h = WORD_H if self.show_words else 0
        wave_top, wave_h = RULER_H + BLOCK_H + 2, h - RULER_H - BLOCK_H - 4 - lane_h
        mid = wave_top + wave_h / 2

        # Area past the end of the audio.
        x_end = self.x_at(self.duration)
        if x_end < w:
            p.fillRect(QRectF(x_end, RULER_H, w - x_end, h - RULER_H), QColor("#e5e7eb"))

        # Cleanup-plan blocks: numbered bands in the strip, faint tint over the waveform.
        p.fillRect(QRectF(0, RULER_H, w, BLOCK_H), QColor("#fffbeb"))
        for i, (a, b) in enumerate(self.blocks):
            xa, xb = self.x_at(a), self.x_at(b)
            if xb < 0 or xa > w:
                continue
            active = i == self.active_block
            band = QRectF(xa, RULER_H + 1, max(xb - xa, 2), BLOCK_H - 2)
            p.fillRect(band, QColor("#b45309" if active else "#f59e0b"))
            p.fillRect(QRectF(xa, RULER_H + BLOCK_H, max(xb - xa, 1), h - RULER_H - BLOCK_H),
                       QColor(245, 158, 11, 45 if active else 25))
            p.setPen(QColor("white"))
            p.drawText(band, Qt.AlignCenter, str(i + 1))

        # Selection under the bars.
        if self.selection:
            a, b = (self.x_at(t) for t in self.selection)
            p.fillRect(QRectF(a, RULER_H, max(b - a, 1), h - RULER_H), QColor(59, 130, 246, 55))

        # Bars: one per pixel column, peak of the envelope values it covers.
        cols = int(min(w, max(0, x_end)))
        if cols > 0:
            edges = self.view_start + np.arange(cols + 1) * (self.view_span / w)
            idx = np.clip((edges * ENV_RATE).astype(np.int64), 0, len(self.disp) - 1)
            lo, hi = idx[0], idx[-1] + 1
            peaks = np.maximum.reduceat(self.disp[lo:hi], idx[:-1] - lo)
            mids = np.clip(((edges[:-1] + edges[1:]) / 2 * ENV_RATE).astype(np.int64), 0, len(self.disp) - 1)
            colors = self.cidx[mids]
            half = wave_h / 2
            for k in np.unique(colors):
                xs = np.flatnonzero(colors == k)
                pen = QPen(self.palette[int(k)] if k < len(self.palette) else QColor(NEUTRAL_COLOR))
                pen.setWidthF(1.0)
                p.setPen(pen)
                p.drawLines([QLineF(x + 0.5, mid - max(0.5, peaks[x] * half), x + 0.5,
                                    mid + max(0.5, peaks[x] * half)) for x in xs])

        if self.show_words:
            self._paint_words(p, w, h)

        # Ruler ticks and labels.
        step = next((s for s in _TICKS if s / self.view_span * w >= 80), _TICKS[-1])
        p.setPen(QColor("#475569"))
        f = QFont(p.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8))
        p.setFont(f)
        t = math.floor(self.view_start / step) * step
        while t <= self.view_start + self.view_span:
            x = self.x_at(t)
            if x >= 0:
                p.drawLine(QPointF(x, RULER_H - 5), QPointF(x, RULER_H))
                p.drawText(QPointF(x + 3, RULER_H - 4), fmt_time(t, precise=step < 1))
            t += step

        # Selection edges, playhead, hover time.
        if self.selection:
            p.setPen(QPen(QColor("#2563eb"), 1.5))
            for t in self.selection:
                x = self.x_at(t)
                p.drawLine(QPointF(x, RULER_H), QPointF(x, h))
        x = self.x_at(self.playhead)
        if 0 <= x <= w:
            p.setPen(QPen(QColor("#dc2626"), 2))
            p.drawLine(QPointF(x, RULER_H), QPointF(x, h))
        if self._hover_t is not None:
            p.setPen(QColor("#334155"))
            p.drawText(QRectF(0, RULER_H + 2, w - 6, 14), Qt.AlignRight, fmt_time(self._hover_t, precise=True))


    def _paint_words(self, p: QPainter, w: int, h: int):
        top = h - WORD_H
        p.fillRect(QRectF(0, top, w, WORD_H), QColor("#f1f5f9"))
        p.setPen(QColor("#cbd5e1"))
        p.drawLine(QPointF(0, top), QPointF(w, top))
        base = QFont(p.font())
        italic = QFont(base)
        italic.setItalic(True)
        words = self.visible_words()
        # Sound tags (always shown, even zoomed out): purple boxes. The label is drawn
        # only when words aren't — once in the transcript, the "(label)" word carries it.
        for a, b, label in self.tags:
            xa, xb = self.x_at(a), self.x_at(b)
            if xb < 0 or xa > w:
                continue
            r = QRectF(xa, top + 1, max(xb - xa, 3), WORD_H - 2)
            p.fillRect(r, QColor(124, 58, 237, 40))
            p.setPen(QColor(TAG_COLOR))
            p.drawRect(r)
            if not words:
                p.setFont(italic)
                p.drawText(QRectF(xa + 2, top, max(xb - xa, 120), WORD_H), Qt.AlignVCenter | Qt.AlignLeft,
                           f"({label})")
        if not words:
            if self.words and self.view_span > WORDS_MAX_SPAN:
                p.setFont(base)
                p.setPen(QColor("#94a3b8"))
                p.drawText(QRectF(0, top, w - 6, WORD_H), Qt.AlignVCenter | Qt.AlignRight,
                           f"zoom in (≤ {WORDS_MAX_SPAN:.0f} s visible) to see words")
            return
        for i, (t0, t1, text, color, is_italic) in enumerate(words):
            x0 = self.x_at(t0)
            x_next = self.x_at(words[i + 1][0]) if i + 1 < len(words) else self.x_at(t1) + 200
            avail = min(x_next, w + 200) - x0 - 3
            if avail < 8 or x0 > w:
                continue
            if t0 <= self.playhead < t1:  # the word being spoken
                p.fillRect(QRectF(x0, top + 1, max(self.x_at(t1) - x0, 4), WORD_H - 2), QColor("#fde68a"))
            font = italic if is_italic else base
            p.setFont(font)
            p.setPen(QColor(color))
            shown = QFontMetrics(font).elidedText(text, Qt.ElideRight, int(avail))
            p.drawText(QRectF(x0 + 1, top, avail, WORD_H), Qt.AlignVCenter | Qt.AlignLeft, shown)
        p.setFont(base)


class WaveformPanel(QWidget):
    """Toolbar + WaveformView + scrollbar."""

    selectionChanged = Signal(object)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Waveform"))
        for text, tip, slot in [("−", "Zoom out (or mouse wheel)", lambda: self.view.zoom(1 / 1.6)),
                                ("+", "Zoom in (or mouse wheel)", lambda: self.view.zoom(1.6)),
                                ("Fit", "Show the whole recording", lambda: self.view.fit())]:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        hint = QLabel("drag to select a range · wheel to zoom · Shift+wheel to scroll")
        hint.setStyleSheet("color: #6b7280;")
        bar.addWidget(hint)
        bar.addStretch(1)
        self.words_check = QCheckBox("Words")
        self.words_check.setToolTip(f"Show each word under its audio when zoomed in "
                                    f"(≤ {WORDS_MAX_SPAN:.0f} s visible); it scrolls with playback")
        self.words_check.toggled.connect(lambda on: self.view.set_show_words(on))
        bar.addWidget(self.words_check)
        self.tag_btn = QPushButton("Tag sound…")
        self.tag_btn.setToolTip("Label the selected sound (sneeze, horn, bell…); it's inserted into the\n"
                                "transcript in italics between the words where it happened")
        self.tag_btn.setEnabled(False)
        bar.addWidget(self.tag_btn)
        self.sel_label = QLabel("")
        self.sel_label.setStyleSheet("color: #1d4ed8; font-family: monospace;")
        bar.addWidget(self.sel_label)
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setToolTip("Repeat the selected range instead of stopping at its end")
        bar.addWidget(self.loop_check)
        self.clear_btn = QPushButton("Clear selection")
        self.clear_btn.setEnabled(False)
        bar.addWidget(self.clear_btn)
        lay.addLayout(bar)

        self.view = WaveformView()
        lay.addWidget(self.view, 1)
        self.scroll = QScrollBar(Qt.Horizontal)
        lay.addWidget(self.scroll)

        self.clear_btn.clicked.connect(lambda: self.view.set_selection(None))
        self.view.selectionChanged.connect(self._selection_changed)
        self.view.viewChanged.connect(self._sync_scroll)
        self.scroll.valueChanged.connect(lambda v: self.view.set_view(v / 100))
        self._sync_scroll()

    @property
    def selection(self):
        return self.view.selection

    @property
    def loop(self) -> bool:
        return self.loop_check.isChecked()

    def _selection_changed(self, sel):
        self.clear_btn.setEnabled(sel is not None)
        self.tag_btn.setEnabled(sel is not None)
        self.sel_label.setText("" if sel is None else
                               f"Selection {fmt_time(sel[0], True)} – {fmt_time(sel[1], True)} "
                               f"({sel[1] - sel[0]:.1f} s)")
        self.selectionChanged.emit(sel)

    def _sync_scroll(self):
        v = self.view
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(max(0.0, v.duration - v.view_span) * 100))
        self.scroll.setPageStep(int(v.view_span * 100))
        self.scroll.setSingleStep(max(1, int(v.view_span * 10)))
        self.scroll.setValue(int(v.view_start * 100))
        self.scroll.blockSignals(False)
