"""Application logging: optional per-session log file of steps, commands and errors.

File logging is switched on/off from the UI (persisted there); the file is
``$XDG_STATE_HOME/transcript-studio/logs/transcript-studio-<timestamp>.log``.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("transcript_studio")
LOG_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "transcript-studio" / "logs"
KEEP_LOGS = 30  # oldest session logs beyond this are deleted

_FORMAT = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
_file_handler: logging.FileHandler | None = None


def setup() -> None:
    """Console gets warnings and up; the logger itself passes everything to handlers."""
    if getattr(setup, "_done", False):
        return
    log.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(_FORMAT)
    log.addHandler(console)
    setup._done = True


def current_log_file() -> Path | None:
    return Path(_file_handler.baseFilename) if _file_handler else None


def set_file_logging(enabled: bool) -> Path | None:
    """Turn the session log file on/off; returns its path when on."""
    global _file_handler
    setup()
    if enabled and _file_handler is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _prune()
        path = LOG_DIR / f"transcript-studio-{datetime.now():%Y%m%d-%H%M%S}.log"
        _file_handler = logging.FileHandler(path, encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        _file_handler.setFormatter(_FORMAT)
        log.addHandler(_file_handler)
        log.info("Log file opened: %s", path)
        _log_environment()
    elif not enabled and _file_handler is not None:
        log.info("Log file closed by user")
        log.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
    return current_log_file()


def _prune() -> None:
    logs = sorted(LOG_DIR.glob("transcript-studio-*.log"))
    for old in logs[:max(0, len(logs) - KEEP_LOGS + 1)]:
        old.unlink(missing_ok=True)


def _log_environment() -> None:
    log.info("Python %s on %s", platform.python_version(), platform.platform())
    for mod in ("PySide6", "faster_whisper", "ctranslate2", "torch", "speechbrain", "resemblyzer"):
        try:
            m = __import__(mod)
            log.info("  %s %s", mod, getattr(m, "__version__", "?"))
        except Exception as e:  # missing optional dependency is worth recording, not fatal
            log.info("  %s not importable (%s)", mod, e)
