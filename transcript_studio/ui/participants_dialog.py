"""Transcript → Name Participants… dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLabel, QLineEdit, QVBoxLayout,
)

from ..export import format_timestamp, speaker_style
from ..model import Session


def participant_summary(session: Session) -> dict[int, dict]:
    """Per speaker: talk time, first time they speak, and their first words (to help identify them)."""
    out: dict[int, dict] = {}
    for para in session.paragraphs:
        if para.is_nonspeech or para.speaker_id is None:
            continue
        info = out.setdefault(para.speaker_id, {"talk": 0.0, "first": para.start_time, "first_text": ""})
        for sent in para.sentences:
            info["talk"] += max(0.0, sent.end_time - sent.start_time)
            if not info["first_text"] and len(sent.text.split()) >= 3:
                info["first_text"] = sent.text
    return out


class ParticipantsDialog(QDialog):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Name participants")
        self.session = session
        lay = QVBoxLayout(self)
        intro = QLabel("Give each participant a name. It replaces “Participant N” in the transcript and the "
                       "exported markdown. Leave a box empty to keep the number.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        self.edits: dict[int, QLineEdit] = {}
        summary = participant_summary(session)
        for row, sid in enumerate(sorted(summary)):
            info = summary[sid]
            emoji, color = speaker_style(sid)
            label = QLabel(f"{emoji} <b style='color:{color}'>Participant {sid}</b>")
            edit = QLineEdit(session.speaker_names.get(str(sid), ""))
            edit.setPlaceholderText(f"Participant {sid}")
            edit.setMinimumWidth(220)
            text = info["first_text"] or ""
            hint = QLabel(f"{format_timestamp(info['talk'])} talk time · first speaks at "
                          f"{format_timestamp(info['first'])}"
                          + (f"<br><i>“{text[:70]}{'…' if len(text) > 70 else ''}”</i>" if text else ""))
            hint.setStyleSheet("color: #6b7280;")
            grid.addWidget(label, row * 2, 0)
            grid.addWidget(edit, row * 2, 1)
            grid.addWidget(hint, row * 2 + 1, 1)
            self.edits[sid] = edit
        lay.addLayout(grid)
        if not self.edits:
            lay.addWidget(QLabel("No participants in this transcript yet."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def names(self) -> dict[str, str]:
        """Names entered, keyed by str(speaker id); names from speakers not shown are kept."""
        names = {k: v for k, v in self.session.speaker_names.items() if int(k) not in self.edits}
        for sid, edit in self.edits.items():
            if edit.text().strip():
                names[str(sid)] = edit.text().strip()
        return names
