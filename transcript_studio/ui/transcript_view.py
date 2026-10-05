"""Editable, color-coded transcript view (spec §9 "Transcript panel").

Sync is one-way: the main window calls ``highlight_at(seconds)`` from the
player position. Nothing in this widget ever touches playback — clicking text
only places the edit cursor. The right-click menu can ask the main window to
*center the waveform view* on a word (view only) or to select a sentence's
time range; a waveform selection shows up here as inverted text.
"""

from __future__ import annotations

import bisect

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeyEvent, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from ..export import LOW_CONF_COLOR, NONSPEECH_COLOR, NONSPEECH_EMOJI, SPEAKER_PALETTE, format_timestamp, speaker_style
from ..model import Paragraph, Sentence, Session
from ..sounds import ANNOTATION_RE

HIGHLIGHT_BG = "#fef3c7"


def fmt_seconds(t: float) -> str:
    m, sec = divmod(max(0.0, t), 60)
    return f"{int(m) // 60}:{int(m) % 60:02d}:{sec:04.1f}" if m >= 60 else f"{int(m):02d}:{sec:04.1f}"
NONSPEECH_CHOICE = 0  # combo data value for "non-speech"


class AnnotationHighlighter(QSyntaxHighlighter):
    """Shows sound annotations like "(sneeze)" or "[inaudible]" in italics."""

    def highlightBlock(self, text: str):
        fmt = QTextCharFormat()
        fmt.setFontItalic(True)
        for m in ANNOTATION_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), fmt)


class SentenceEdit(QTextEdit):
    """Plain-text, single-paragraph editor that grows to fit its content."""

    edited = Signal()

    def __init__(self, text: str):
        super().__init__()
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setPlainText(text)
        self._highlighter = AnnotationHighlighter(self.document())
        self.document().contentsChanged.connect(self._fit)
        self.textChanged.connect(self.edited)

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            return  # sentences are single lines in the export
        super().keyPressEvent(e)

    def insertFromMimeData(self, source):
        self.insertPlainText(" ".join(source.text().split()))  # pasted newlines -> spaces

    def contextMenuEvent(self, e):
        self.build_context_menu(e.pos()).exec(e.globalPos())

    def build_context_menu(self, pos: QPoint) -> QMenu:
        """Standard edit menu plus the row's audio actions (set via ``audio_actions``)."""
        menu = self.createStandardContextMenu(pos)
        actions = getattr(self, "audio_actions", None)
        if actions:
            first = menu.actions()[0] if menu.actions() else None
            for text, slot in actions(self.cursorForPosition(pos).position()):
                act = QAction(text, menu)
                act.triggered.connect(slot)
                menu.insertAction(first, act)  # audio actions go on top
            if first is not None:
                menu.insertSeparator(first)
        return menu

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        self.document().setTextWidth(self.viewport().width())
        h = int(self.document().size().height()) + 2 * self.frameWidth() + 4
        if h != self.height():
            self.setFixedHeight(h)


class SentenceRow(QWidget):
    changed = Signal()
    centerRequested = Signal(float)
    selectRequested = Signal(object)

    def __init__(self, para: Paragraph, sent: Sentence):
        super().__init__()
        self.para, self.sent = para, sent
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.num = QLabel()
        self.num.setFixedWidth(52)
        self.num.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.num.setStyleSheet("font-weight: bold; padding-top: 4px;")
        lay.addWidget(self.num)

        self.flag = QToolButton()
        self.flag.setCheckable(True)
        self.flag.setChecked(sent.low_confidence)
        self.flag.setToolTip("Low-confidence / possibly unintelligible (toggle)")
        self.flag.setAutoRaise(True)
        self.flag.toggled.connect(self._flag_toggled)
        lay.addWidget(self.flag, 0, Qt.AlignTop)

        self.edit = SentenceEdit(sent.text)
        self.edit.edited.connect(self._text_edited)
        self.edit.audio_actions = self._audio_actions
        lay.addWidget(self.edit, 1)
        self.highlighted = False
        self.refresh()

    # ---- text position <-> audio time

    def _token_times(self) -> list[tuple[int, int, float, float]]:
        """(char start, char end, start time, end time) per word of the current text.

        Uses the word timestamps while the text still has the same number of
        words; after edits that change the word count, spreads the sentence's
        time range across the characters instead.
        """
        return self.sent.token_times(self.edit.toPlainText())

    def time_at_char(self, pos: int) -> float:
        for _a, b, t0, _t1 in self._token_times():
            if pos < b:  # inside this word, or in the space just before it
                return t0
        return self.sent.end_time

    def _audio_actions(self, char_pos: int):
        t = self.time_at_char(char_pos)
        return [
            (f"Center waveform here ({fmt_seconds(t)})", lambda: self.centerRequested.emit(t)),
            (f"Select this sentence in the waveform ({fmt_seconds(self.sent.start_time)}–"
             f"{fmt_seconds(self.sent.end_time)})",
             lambda: self.selectRequested.emit((self.sent.start_time, self.sent.end_time))),
        ]

    def mark_audio_selection(self, sel: tuple[float, float] | None) -> bool:
        """Invert the words inside the waveform selection; returns True if any matched."""
        selections = []
        if sel is not None and self.sent.end_time > sel[0] and self.sent.start_time < sel[1]:
            fg, bg = "#ffffff", self._text_color()
            for a, b, t0, t1 in self._token_times():
                if t1 > sel[0] and t0 < sel[1]:
                    es = QTextEdit.ExtraSelection()
                    cur = QTextCursor(self.edit.document())
                    cur.setPosition(a)
                    cur.setPosition(b, QTextCursor.KeepAnchor)
                    es.cursor = cur
                    es.format.setBackground(QColor(bg))
                    es.format.setForeground(QColor(fg))
                    selections.append(es)
            # Join neighbouring words so the inverted run has no gaps between them.
            for left, right in zip(selections, selections[1:]):
                if right.cursor.selectionStart() - left.cursor.selectionEnd() <= 1:
                    gap = QTextEdit.ExtraSelection()
                    cur = QTextCursor(self.edit.document())
                    cur.setPosition(left.cursor.selectionEnd())
                    cur.setPosition(right.cursor.selectionStart(), QTextCursor.KeepAnchor)
                    gap.cursor, gap.format = cur, left.format
                    selections.append(gap)
        self.edit.setExtraSelections(selections)
        return bool(selections)

    def _text_color(self) -> str:
        if self.para.is_nonspeech:
            return NONSPEECH_COLOR
        if self.sent.low_confidence:
            return LOW_CONF_COLOR
        return speaker_style(self.para.speaker_id)[1]

    def _flag_toggled(self, on: bool):
        self.sent.low_confidence = on
        self.refresh()
        self.changed.emit()

    def _text_edited(self):
        self.sent.text = self.edit.toPlainText().strip()
        self.changed.emit()

    def refresh(self):
        self.num.setText(f"{self.para.index}.{self.sent.index}")
        nonspeech = self.para.is_nonspeech
        self.flag.setVisible(not nonspeech)
        self.flag.setText("🔴" if self.sent.low_confidence else "○")
        if nonspeech:
            color, style = NONSPEECH_COLOR, "italic"
        elif self.sent.low_confidence:
            color, style = LOW_CONF_COLOR, "normal"
        else:
            color, style = speaker_style(self.para.speaker_id)[1], "normal"
        bg = HIGHLIGHT_BG if self.highlighted else "transparent"
        self.edit.setStyleSheet(f"QTextEdit {{ color: {color}; font-style: {style}; background: {bg}; }}")

    def set_highlighted(self, on: bool):
        if on != self.highlighted:
            self.highlighted = on
            self.refresh()


class ParagraphBlock(QWidget):
    changed = Signal()
    centerRequested = Signal(float)
    selectRequested = Signal(object)

    def __init__(self, para: Paragraph, max_speakers: int, session: Session):
        super().__init__()
        self.para, self.session = para, session
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 4)
        lay.setSpacing(2)

        head = QHBoxLayout()
        self.title = QLabel()
        f = QFont()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.1)
        self.title.setFont(f)
        head.addWidget(self.title)

        self.speaker = QComboBox()
        self.speaker.setToolTip("Reassign this paragraph's speaker")
        # Qt under-measures color-emoji glyphs, so AdjustToContents truncates the label.
        self.speaker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.speaker.setMinimumContentsLength(16)
        self.speaker.addItem(f"{NONSPEECH_EMOJI} Non-speech", NONSPEECH_CHOICE)
        for sid in range(1, max_speakers + 1):
            self.speaker.addItem("", sid)
        self.relabel()
        self._sync_combo()
        self.speaker.currentIndexChanged.connect(self._speaker_changed)
        head.addWidget(self.speaker)

        self.time = QLabel()
        self.time.setStyleSheet("color: #6b7280; font-style: italic;")
        head.addWidget(self.time)
        head.addStretch(1)
        lay.addLayout(head)

        self.rows = []
        for sent in para.sentences:
            row = SentenceRow(para, sent)
            row.changed.connect(self.changed)
            row.centerRequested.connect(self.centerRequested)
            row.selectRequested.connect(self.selectRequested)
            self.rows.append(row)
            lay.addWidget(row)
        self.refresh()

    def relabel(self):
        """Refresh speaker names in the dropdown (after Name Participants)."""
        longest = 0
        for i in range(1, self.speaker.count()):
            sid = self.speaker.itemData(i)
            label = self.session.speaker_label(sid)
            self.speaker.setItemText(i, f"{speaker_style(sid)[0]} {label}")
            longest = max(longest, len(label))
        self.speaker.setMinimumContentsLength(max(16, longest + 4))

    def _sync_combo(self):
        value = NONSPEECH_CHOICE if self.para.is_nonspeech else self.para.speaker_id
        self.speaker.blockSignals(True)
        self.speaker.setCurrentIndex(max(0, self.speaker.findData(value)))
        self.speaker.blockSignals(False)

    def _speaker_changed(self):
        value = self.speaker.currentData()
        if value == NONSPEECH_CHOICE:
            self.para.is_nonspeech, self.para.speaker_id = True, None
        else:
            self.para.is_nonspeech, self.para.speaker_id = False, value
        self.refresh()
        self.changed.emit()

    def refresh(self):
        self.title.setText(f"{self.para.index}.")
        self.time.setText(format_timestamp(self.para.start_time))
        for row in self.rows:
            row.refresh()


class TranscriptView(QScrollArea):
    """Scrollable list of ParagraphBlocks; ``changed`` fires on any user edit."""

    changed = Signal()
    centerRequested = Signal(float)  # right-click → center the waveform view on this time
    selectRequested = Signal(object)  # right-click → select this (start, end) in the waveform

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.session: Session | None = None
        self.follow = True
        self._rows: list[SentenceRow] = []
        self._starts: list[float] = []
        self._current: SentenceRow | None = None
        self._placeholder("Open an audio file and transcribe it, or open a saved project.")

    def _placeholder(self, text: str):
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #6b7280;")
        self.setWidget(label)
        self._rows, self._starts, self._current, self._blocks = [], [], None, []

    def set_session(self, session: Session | None):
        self.session = session
        if session is None or not session.paragraphs:
            self._placeholder("No transcript.")
            return
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(12, 8, 12, 8)
        max_speakers = max([len(SPEAKER_PALETTE)] + session.speaker_ids())
        self._rows, self._blocks = [], []
        for para in session.paragraphs:
            block = ParagraphBlock(para, max_speakers, session)
            self._blocks.append(block)
            block.changed.connect(self.changed)
            block.centerRequested.connect(self.centerRequested)
            block.selectRequested.connect(self.selectRequested)
            lay.addWidget(block)
            self._rows += block.rows
        lay.addStretch(1)
        self.setWidget(container)
        # Rows are in transcript order; sentence start times are monotonic.
        self._starts = [r.sent.start_time for r in self._rows]
        self._current = None

    def set_audio_selection(self, sel: tuple[float, float] | None, scroll: bool = True):
        """Show a waveform selection as inverted text and scroll to its first match."""
        first = None
        for row in self._rows:
            if row.mark_audio_selection(sel) and first is None:
                first = row
        if first is not None and scroll:
            self.ensureWidgetVisible(first, 0, self.viewport().height() // 3)
        return first

    def relabel_speakers(self):
        for block in getattr(self, "_blocks", []):
            block.relabel()

    def highlight_at(self, seconds: float):
        """Highlight (and optionally scroll to) the sentence playing at `seconds`."""
        if not self._rows:
            return
        i = bisect.bisect_right(self._starts, seconds) - 1
        # Between sentences, keep the previous one lit until the next starts.
        row = self._rows[i] if i >= 0 else None
        if row is self._current:
            return
        if self._current is not None:
            self._current.set_highlighted(False)
        self._current = row
        if row is not None:
            row.set_highlighted(True)
            if self.follow:
                self.ensureWidgetVisible(row, 0, self.viewport().height() // 3)
