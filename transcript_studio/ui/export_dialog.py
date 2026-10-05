"""File → Export…: choose format (Markdown / PDF / Word), details header on/off, and where to save."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QRadioButton, QVBoxLayout,
)

from ..export_formats import FORMATS

FILTERS = {"md": "Markdown (*.md)", "pdf": "PDF (*.pdf)", "docx": "Word document (*.docx)"}


class ExportDialog(QDialog):
    def __init__(self, folder: Path, stem: str, fmt: str = "md", details: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export transcript")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("<b>Format</b>"))
        row = QHBoxLayout()
        self.radios: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)
        for key, label in FORMATS.items():
            r = QRadioButton(label)
            self.radios[key] = r
            group.addButton(r)
            row.addWidget(r)
            r.toggled.connect(lambda on, k=key: on and self._format_changed(k))
        row.addStretch(1)
        lay.addLayout(row)

        self.details = QCheckBox("Include file && transcription details (header tables)")
        self.details.setToolTip("Recording specs, transcription stats and talk time per participant,\n"
                                "after the intro. Untick for just the transcript.")
        self.details.setChecked(details)
        lay.addWidget(self.details)

        lay.addWidget(QLabel("<b>Save to</b>"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(str(Path(folder) / f"{stem}.{fmt}"))
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.export_btn = buttons.addButton("Export", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.radios.get(fmt, self.radios["md"]).setChecked(True)

    # ---- results

    def fmt(self) -> str:
        return next(k for k, r in self.radios.items() if r.isChecked())

    def path(self) -> Path:
        return Path(self.path_edit.text().strip()).expanduser()

    def include_details(self) -> bool:
        return self.details.isChecked()

    # ---- behaviour

    def _format_changed(self, key: str):
        text = self.path_edit.text().strip()
        if text:  # keep folder and name, swap the extension
            p = Path(text)
            self.path_edit.setText(str(p.with_suffix(f".{key}")))

    def _browse(self):
        fmt = self.fmt()
        fn, _ = QFileDialog.getSaveFileName(self, "Export transcript", str(self.path()), FILTERS[fmt])
        if fn:
            p = Path(fn)
            self.path_edit.setText(str(p if p.suffix.lower() == f".{fmt}" else p.with_suffix(f".{fmt}")))

    def _accept(self):
        p = self.path()
        if not self.path_edit.text().strip():
            QMessageBox.warning(self, "Export", "Choose where to save the file.")
            return
        if p.suffix.lower() != f".{self.fmt()}":
            p = p.with_suffix(f".{self.fmt()}")
            self.path_edit.setText(str(p))
        if not p.parent.exists():
            QMessageBox.warning(self, "Export", f"The folder doesn't exist:\n{p.parent}")
            return
        if p.exists() and QMessageBox.question(
                self, "Export", f"{p.name} already exists. Replace it?") != QMessageBox.Yes:
            return
        self.accept()
