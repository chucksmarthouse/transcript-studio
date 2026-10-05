"""Help menu: User Manual window and About box."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QSplitter, QTextBrowser,
    QVBoxLayout,
)

from .. import __version__, logs

MANUAL = Path(__file__).resolve().parent.parent / "docs" / "user_manual.md"
ROOT = Path(__file__).resolve().parent.parent.parent


def manual_sections(text: str) -> list[tuple[str, str]]:
    """Split the manual into (title, markdown) at its '## ' headings."""
    parts = re.split(r"^## ", text, flags=re.M)
    return [(p.splitlines()[0].strip(), "## " + p) for p in parts[1:]]


class ManualWindow(QDialog):
    """Non-modal manual: section list on the left, the section on the right, and a find box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcript Studio — User Manual")
        self.resize(1000, 720)
        self.sections = manual_sections(MANUAL.read_text(encoding="utf-8"))

        lay = QVBoxLayout(self)
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find:"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("search the whole manual, Enter for next match")
        self.find_edit.returnPressed.connect(self.find_next)
        find_row.addWidget(self.find_edit, 1)
        nxt = QPushButton("Next")
        nxt.clicked.connect(self.find_next)
        find_row.addWidget(nxt)
        self.find_status = QLabel("")
        self.find_status.setStyleSheet("color: #6b7280;")
        find_row.addWidget(self.find_status)
        lay.addLayout(find_row)

        split = QSplitter(Qt.Horizontal)
        self.toc = QListWidget()
        self.toc.addItems([t for t, _ in self.sections])
        self.toc.setMaximumWidth(260)
        self.toc.currentRowChanged.connect(self.show_section)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.document().setDefaultStyleSheet("table { border-collapse: collapse; } "
                                                     "td, th { padding: 3px 8px; }")
        split.addWidget(self.toc)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        self.toc.setCurrentRow(0)

    def show_section(self, row: int):
        if 0 <= row < len(self.sections):
            self.browser.setMarkdown(self.sections[row][1])

    def find_next(self):
        query = self.find_edit.text().strip()
        if not query:
            return
        if self.browser.find(query):  # next match in the current section
            self.find_status.setText("")
            return
        n = len(self.sections)
        start = self.toc.currentRow()
        for step in range(1, n + 1):  # then the following sections, wrapping round
            row = (start + step) % n
            if query.lower() in self.sections[row][1].lower():
                self.toc.setCurrentRow(row)
                self.browser.moveCursor(self.browser.textCursor().MoveOperation.Start)
                self.browser.find(query)
                self.find_status.setText(f"in “{self.sections[row][0]}”")
                return
        self.find_status.setText("not found")


@lru_cache(maxsize=1)
def gpu_name() -> str:
    """Graphics card as the transcription engines see it."""
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            if out:
                return out.splitlines()[0] + " (NVIDIA CUDA)"
        except Exception:
            pass
    if shutil.which("vulkaninfo"):
        try:
            out = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=5).stdout
            names = [n.strip() for n in re.findall(r"deviceName\s*=\s*(.+)", out) if "llvmpipe" not in n]
            if names:
                return names[0] + " (Vulkan)"
        except Exception:
            pass
    return "no GPU detected"


def _version(module: str) -> str:
    try:
        return getattr(__import__(module), "__version__", "installed")
    except Exception:
        return "not installed"


def about_html() -> str:
    from ..backends import BACKENDS, best_backend

    engines = "".join(
        f"<li>{b.label}: {'<b>available</b>' if ok else f'unavailable — {why}'}</li>"
        for b in BACKENDS for ok, why in [b.available()])
    try:
        default = best_backend().label
    except RuntimeError:
        default = "none"
    wcpp = ROOT / "vendor" / "whisper.cpp"
    try:
        wcpp_rev = subprocess.run(["git", "-C", str(wcpp), "log", "-1", "--format=%h %cs"],
                                  capture_output=True, text=True, timeout=3).stdout.strip() or "?"
    except Exception:
        wcpp_rev = "?"
    log_file = logs.current_log_file()
    return f"""
<h2>Transcript Studio {__version__}</h2>
<p>Local transcription &amp; review: transcribe recordings on this computer, separate speakers,
clean up hard-to-hear audio, correct the colour-coded transcript while listening, and export
markdown. No cloud service, account or token is needed.</p>

<h3>This computer</h3>
<ul>
<li>Graphics: {gpu_name()}</li>
<li>Default engine: <b>{default}</b></li>
{engines}
</ul>

<h3>Software</h3>
<ul>
<li>Python {platform.python_version()} · Qt {qVersion()} · PySide6 {pyside_version}</li>
<li>whisper.cpp {wcpp_rev} (Vulkan build) · faster-whisper {_version('faster_whisper')} ·
    CTranslate2 {_version('ctranslate2')}</li>
<li>speechbrain {_version('speechbrain')} (ECAPA speaker model) · resemblyzer · scikit-learn
    {_version('sklearn')} · ffmpeg</li>
</ul>

<h3>Folders</h3>
<ul>
<li>Program: {ROOT}</li>
<li>Logs: {logs.LOG_DIR}{' (current: ' + log_file.name + ')' if log_file else ' (logging is off)'}</li>
<li>Models: {ROOT / 'vendor' / 'whisper.cpp' / 'models'}, ~/.cache/huggingface</li>
</ul>

<h3>Credits &amp; licences</h3>
<p style="font-size: small">
Qt for Python / PySide6 (LGPLv3) · whisper.cpp &amp; ggml (MIT) · OpenAI Whisper models (MIT) ·
faster-whisper (MIT) · CTranslate2 (MIT) · SpeechBrain &amp; spkrec-ecapa-voxceleb (Apache 2.0) ·
Resemblyzer (Apache 2.0) · scikit-learn (BSD) · NumPy / SciPy (BSD) · FFmpeg (LGPL/GPL) ·
RNNoise &amp; arnndn models (BSD) · Silero VAD (MIT). Test clips: NASA “Houston We Have a Podcast”
(public domain).</p>
"""


def show_about(parent=None):
    box = QMessageBox(parent)
    box.setWindowTitle("About Transcript Studio")
    box.setTextFormat(Qt.RichText)
    box.setText(about_html())
    box.setStyleSheet("QLabel { min-width: 620px; }")  # the default box is too narrow for the details
    box.setStandardButtons(QMessageBox.Ok)
    box.exec()
