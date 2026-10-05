"""Launch the Transcript Studio GUI:  .venv/bin/python -m transcript_studio [audio-or-project]"""

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import logs
from .ui.main_window import MainWindow


def main():
    logs.setup()
    app = QApplication(sys.argv)
    app.setApplicationName("Transcript Studio")
    # Matches transcript-studio.desktop (tools/install_desktop.sh), so the dock
    # groups the window with its launcher icon instead of a generic one.
    app.setDesktopFileName("transcript-studio")
    icon = Path(__file__).resolve().parent.parent / "assets" / "transcript-studio.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.name.endswith(".json"):
            win.load_project(p)
        else:
            win.load_audio(p)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
