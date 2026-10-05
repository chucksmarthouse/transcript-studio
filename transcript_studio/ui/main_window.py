"""Main window: transport + boost + transcription controls around the TranscriptView (spec §9)."""

from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QInputDialog, QTabWidget, QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox, QStyle,
    QToolButton, QVBoxLayout, QWidget,
)

from .. import audio as audio_mod
from .. import logs
from .. import sounds
from ..backends import BACKENDS, Backend, Cancelled, WhisperCppBackend, best_backend
from .. import export_formats
from ..export import format_timestamp
from ..logs import log
from ..model import Session
from ..pipeline import PipelineOptions, run as run_pipeline
from ..progress import ProgressEvent, Reporter
from ..envelope import compute_envelope
from ..sysstats import SystemStats
from .cleanup_panel import WHOLE, CleanupPanel, fmt_t
from .export_dialog import ExportDialog
from .help import ManualWindow, show_about
from .participants_dialog import ParticipantsDialog
from .transcript_view import TranscriptView
from .waveform import WaveformPanel, selection_action

AUDIO_FILTER = "Audio files (*.m4a *.mp3 *.wav *.flac *.ogg *.opus *.aac *.wma *.mp4 *.webm);;All files (*)"
PROJECT_FILTER = "Transcript Studio project (*.tsproj.json);;JSON (*.json)"
DEFAULT_MODEL = "medium.en"


def parse_time(text: str) -> float | None:
    """'1:02:03', '2:03', '123', '123.5' -> seconds."""
    text = text.strip()
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", text):
        return None
    secs = 0.0
    for part in text.split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def single_step(emit, name: str) -> Reporter:
    """A one-step Reporter for simple jobs (conversion, boost)."""
    rep = Reporter(emit, [name])
    rep.step(name)
    return rep


class Worker(QThread):
    """Runs fn(emit, cancel) off the GUI thread; emit() takes ProgressEvents."""

    event = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str, str)  # (step it failed in, message)

    def __init__(self, fn, title: str):
        super().__init__()
        self.fn, self.title = fn, title
        self.cancel = threading.Event()
        self.last_step = title

    def _emit(self, e: ProgressEvent):
        self.last_step = e.name or self.last_step
        self.event.emit(e)

    def run(self):
        try:
            result = self.fn(self._emit, self.cancel)
        except Cancelled:
            log.warning("%s: cancelled", self.title)
            self.failed.emit(self.last_step, "Cancelled.")
        except Exception as e:
            log.exception("%s failed during %s", self.title, self.last_step)
            self.failed.emit(self.last_step, f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1200, 900)
        self.audio_path: Path | None = None
        self.playback_path: Path | None = None  # FLAC copy actually played/transcribed
        self.boosted_path: Path | None = None
        self.project_path: Path | None = None
        self.session: Session | None = None
        self.dirty = False
        self.worker: Worker | None = None
        self._slider_dragging = False
        self.settings = QSettings("transcript-studio", "transcript-studio")
        logs.set_file_logging(self.settings.value("log_to_file", True, type=bool))
        self.sysstats = SystemStats()
        self._sys: dict = {}
        self._job: dict = {}
        self.envelopes: dict[Path, object] = {}  # playback/boosted file -> envelope
        self.boost_description: str | None = None
        self._last_pos: float | None = None
        # Audition: the player may be playing a short processed clip that starts
        # `_src_offset` seconds into the recording. All UI positions are recording time.
        self._src_offset = 0.0
        self._on_clip = False
        self._full_ms = 0
        self._pending_pos: float | None = None  # re-applied once newly set media has loaded
        self._audition: dict | None = None
        self._audition_seq = 0
        self._audition_worker: Worker | None = None
        self._audition_dir = Path(tempfile.mkdtemp(prefix="ts-audition-"))
        self.editing = None  # cleanup-plan entry being edited: None, WHOLE or a block index

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._playback_error)

        central = QWidget()
        root = QVBoxLayout(central)
        root.addWidget(self._build_transport())
        self.wave = WaveformPanel()
        self.wave.setMinimumHeight(150)
        self.wave.selectionChanged.connect(self._selection_changed)
        self.wave.view.blockClicked.connect(self._edit_plan_entry)
        self.wave.tag_btn.clicked.connect(self.tag_sound)
        self.wave.view.tagRemoveRequested.connect(self.remove_sound_tag)
        self.wave.words_check.setChecked(self.settings.value("show_words", True, type=bool))
        self.wave.view.set_show_words(self.wave.words_check.isChecked())
        self.wave.words_check.toggled.connect(lambda on: self.settings.setValue("show_words", on))
        root.addWidget(self.wave)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_cleanup(), "Speech cleanup")
        self.transcribe_box = self._build_transcribe()
        self.tabs.addTab(self.transcribe_box, "Transcription")
        self.tabs.setCurrentWidget(self.transcribe_box)
        root.addWidget(self.tabs)
        root.addLayout(self._build_transcript_bar())
        self.view = TranscriptView()
        self.view.changed.connect(self._mark_dirty)
        self.view.changed.connect(lambda: self.wave.view.set_session(self.session))
        # Edits/recoloring keep the inverted overlay in the new colors (without re-scrolling).
        self.view.changed.connect(lambda: self.view.set_audio_selection(self.wave.selection, scroll=False))
        self.view.centerRequested.connect(self._center_waveform)
        self.view.selectRequested.connect(self._select_sentence)
        root.addWidget(self.view, 1)
        self.setCentralWidget(central)
        self._build_menu()

        # Audio position drives the transcript highlight (one-way).
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(40)
        self.sync_timer.timeout.connect(self._tick)
        self.sync_timer.start()

        # Live job stats (elapsed/ETA/GPU/CPU) while a job runs.
        self.stats_timer = QTimer(self)
        self.stats_timer.setInterval(1000)
        self.stats_timer.timeout.connect(self._tick_stats)

        self._set_audio_enabled(False)
        self._update_title()

    # ------------------------------------------------------------ building

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")

        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            m.addAction(a)
            return a

        act("Open &Audio…", self.open_audio, "Ctrl+O")
        act("Open &Project…", self.open_project, "Ctrl+Shift+O")
        m.addSeparator()
        self.save_act = act("&Save Project", self.save_project, QKeySequence.Save)
        act("Save Project &As…", self.save_project_as, "Ctrl+Shift+S")
        self.export_act = act("&Export… (Markdown, PDF, Word)", self.export_transcript, "Ctrl+E")
        m.addSeparator()
        act("&Quit", self.close, QKeySequence.Quit)

        tm = self.menuBar().addMenu("&Transcript")
        self.name_act = QAction("&Name Participants…", self)
        self.name_act.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self.name_act.setToolTip("Replace “Participant N” with real names")
        self.name_act.triggered.connect(self.name_participants)
        tm.addAction(self.name_act)

        lm = self.menuBar().addMenu("&Log")
        self.log_act = QAction("&Write log file", self, checkable=True)
        self.log_act.setChecked(logs.current_log_file() is not None)
        self.log_act.toggled.connect(self._set_logging)
        lm.addAction(self.log_act)
        view = QAction("&View current log", self)
        view.triggered.connect(self.view_log)
        lm.addAction(view)
        folder = QAction("Open log &folder", self)
        folder.triggered.connect(self.open_log_folder)
        lm.addAction(folder)

        p = self.menuBar().addMenu("&Playback")
        for text, slot, sc in [
            ("Play / Pause", self.toggle_play, "Ctrl+Space"),
            ("Rewind", lambda: self.skip(-self.skip_spin.value()), "Alt+Left"),
            ("Forward", lambda: self.skip(self.skip_spin.value()), "Alt+Right"),
            ("Jump to time…", lambda: (self.jump_edit.setFocus(), self.jump_edit.selectAll()), "Ctrl+J"),
            ("A/B: processed ↔ original (audition)", lambda: self.cleanup.audition_btn.toggle(), "Ctrl+B"),
        ]:
            a = QAction(text, self)
            a.setShortcut(QKeySequence(sc))
            a.triggered.connect(slot)
            p.addAction(a)

        h = self.menuBar().addMenu("&Help")
        manual = QAction("&User Manual", self)
        manual.setShortcut(QKeySequence(QKeySequence.HelpContents))  # F1
        manual.triggered.connect(self.show_manual)
        h.addAction(manual)
        h.addSeparator()
        about = QAction("&About Transcript Studio", self)
        about.triggered.connect(lambda: show_about(self))
        h.addAction(about)

    def _build_transport(self) -> QGroupBox:
        box = QGroupBox("Playback")
        lay = QHBoxLayout(box)
        st = self.style()

        open_btn = QPushButton(st.standardIcon(QStyle.SP_DialogOpenButton), "Open audio…")
        open_btn.clicked.connect(self.open_audio)
        lay.addWidget(open_btn)

        self.back_btn = QToolButton()
        self.back_btn.setIcon(st.standardIcon(QStyle.SP_MediaSeekBackward))
        self.back_btn.setToolTip("Rewind (Alt+Left)")
        self.back_btn.clicked.connect(lambda: self.skip(-self.skip_spin.value()))
        self.play_btn = QToolButton()
        self.play_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("Play / pause (Ctrl+Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        self.fwd_btn = QToolButton()
        self.fwd_btn.setIcon(st.standardIcon(QStyle.SP_MediaSeekForward))
        self.fwd_btn.setToolTip("Forward (Alt+Right)")
        self.fwd_btn.clicked.connect(lambda: self.skip(self.skip_spin.value()))
        for b in (self.back_btn, self.play_btn, self.fwd_btn):
            lay.addWidget(b)

        self.skip_spin = QSpinBox()
        self.skip_spin.setRange(1, 600)
        self.skip_spin.setValue(10)
        self.skip_spin.setSuffix(" s")
        self.skip_spin.setToolTip("Rewind / forward step")
        lay.addWidget(self.skip_spin)
        presets = QButtonGroup(self)
        for n in (5, 10, 30):
            b = QToolButton()
            b.setText(f"{n}s")
            b.clicked.connect(lambda _=False, n=n: self.skip_spin.setValue(n))
            presets.addButton(b)
            lay.addWidget(b)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_slider_dragging", True))
        self.slider.sliderReleased.connect(self._slider_released)
        lay.addWidget(self.slider, 1)

        self.pos_label = QLabel("00:00:00 / 00:00:00")
        self.pos_label.setStyleSheet("font-family: monospace;")
        lay.addWidget(self.pos_label)

        self.jump_edit = QLineEdit()
        self.jump_edit.setPlaceholderText("jump to hh:mm:ss")
        self.jump_edit.setFixedWidth(130)
        self.jump_edit.returnPressed.connect(self._jump)
        lay.addWidget(self.jump_edit)
        jump_btn = QPushButton("Go")
        jump_btn.clicked.connect(self._jump)
        lay.addWidget(jump_btn)
        self._transport_widgets = [self.back_btn, self.play_btn, self.fwd_btn, self.slider, self.jump_edit, jump_btn]
        return box

    def _build_cleanup(self) -> CleanupPanel:
        self.cleanup = CleanupPanel()
        try:
            saved = json.loads(self.settings.value("cleanup_settings", "", type=str) or "{}")
            if saved:
                self.cleanup.set_settings(audio_mod.CleanupSettings.from_dict(saved))
        except (ValueError, TypeError):
            pass
        self.cleanup.settingsChanged.connect(self._cleanup_changed)
        self.boost_btn = self.cleanup.render_btn
        self.cleanup.renderRequested.connect(self.render_plan)
        self.cleanup.saveRequested.connect(self.save_plan_entry)
        self.cleanup.newRequested.connect(self.new_plan_entry)
        self.cleanup.deleteRequested.connect(self.delete_plan_entry)
        self.cleanup.planItemSelected.connect(self._edit_plan_entry)
        self.cleanup.rangeEdited.connect(lambda a, b: self.wave.view.set_selection((a, b)))
        self.cleanup.saveAsRecordingRequested.connect(self.save_rendered_as_recording)
        self.preview_check = self.cleanup.preview_check
        self.preview_check.toggled.connect(self._switch_source)
        self.use_boost_check = self.cleanup.use_check
        self.cleanup.audition_btn.toggled.connect(self._audition_toggled)
        self._audition_timer = QTimer(self)
        self._audition_timer.setSingleShot(True)
        self._audition_timer.setInterval(350)  # debounce re-renders while settings are being changed
        self._audition_timer.timeout.connect(lambda: self._render_audition(play=False))
        return self.cleanup

    def _build_transcribe(self) -> QGroupBox:
        box = QGroupBox("Transcription")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Engine"))
        self.engine_combo = QComboBox()
        best = best_backend()
        log.info("Transcript Studio started; default engine: %s", best.label)
        for b in BACKENDS:
            ok, why = b.available()
            log.info("  engine %-28s %s", b.label, "available" if ok else f"unavailable: {why}")
            self.engine_combo.addItem(b.label + ("" if ok else " — unavailable"), b.key)
            i = self.engine_combo.count() - 1
            if not ok:
                self.engine_combo.model().item(i).setEnabled(False)
                self.engine_combo.setItemData(i, why, Qt.ToolTipRole)
        self.engine_combo.setCurrentIndex(self.engine_combo.findData(best.key))
        self.engine_combo.currentIndexChanged.connect(self._populate_models)
        row.addWidget(self.engine_combo, 1)
        row.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        row.addWidget(self.model_combo, 1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Speakers"))
        self.speakers_spin = QSpinBox()
        self.speakers_spin.setRange(0, 6)
        self.speakers_spin.setSpecialValueText("auto")
        self.speakers_spin.setToolTip("Expected speaker count (auto = pick up to 4)")
        row.addWidget(self.speakers_spin)
        row.addWidget(QLabel("Speaker model"))
        self.speaker_model_combo = QComboBox()
        self.speaker_model_combo.addItem("ECAPA (accurate)", "ecapa")
        self.speaker_model_combo.addItem("resemblyzer (fast)", "resemblyzer")
        self.speaker_model_combo.setToolTip(
            "Voice-embedding model used to tell speakers apart.\n"
            "ECAPA: clearly better on similar voices; ~4-5 min of CPU per hour of audio;\n"
            "one-time ~80 MB download (no account/token).\n"
            "resemblyzer: ~1 min per hour, weaker on similar voices.")
        row.addWidget(self.speaker_model_combo)
        row.addWidget(QLabel("Low-confidence below"))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.0, 1.0)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setValue(0.5)
        self.threshold_spin.setToolTip("Mean word probability below which a sentence is flagged 🔴")
        row.addWidget(self.threshold_spin)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.clicked.connect(self.transcribe)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(lambda: self.worker and self.worker.cancel.set())
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat("%p%")
        row.addWidget(self.transcribe_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.progress, 1)
        lay.addLayout(row)

        self.step_label = QLabel("")
        self.step_label.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.step_label)
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color: #374151;")
        self.detail_label.setWordWrap(True)
        lay.addWidget(self.detail_label)
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #6b7280; font-family: monospace; font-size: 9pt;")
        self.stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.stats_label)

        row = QHBoxLayout()
        self.log_check = QCheckBox("Write log file")
        self.log_check.setToolTip(f"Log every step, command and error to a file in\n{logs.LOG_DIR}")
        self.log_check.setChecked(logs.current_log_file() is not None)
        self.log_check.toggled.connect(self._set_logging)
        view_btn = QPushButton("View log")
        view_btn.clicked.connect(self.view_log)
        row.addWidget(self.log_check)
        row.addWidget(view_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self._populate_models()
        return box

    def _build_transcript_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Title"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("exported as “<Title> Transcript”")
        self.title_edit.textEdited.connect(self._title_edited)
        row.addWidget(self.title_edit, 1)
        self.follow_check = QCheckBox("Follow playback")
        self.follow_check.setChecked(True)
        self.follow_check.setToolTip("Auto-scroll the transcript to the sentence being played")
        self.follow_check.toggled.connect(lambda on: setattr(self.view, "follow", on))
        row.addWidget(self.follow_check)
        save_btn = QPushButton("Save project")
        save_btn.clicked.connect(self.save_project)
        export_btn = QPushButton("Export…")
        export_btn.setToolTip("Export the transcript as Markdown, PDF or Word, with or without the\n"
                              "file & transcription details header (Ctrl+E)")
        export_btn.clicked.connect(self.export_transcript)
        row.addWidget(save_btn)
        row.addWidget(export_btn)
        return row

    # ------------------------------------------------------------ state helpers

    def _backend(self) -> Backend:
        key = self.engine_combo.currentData()
        return next(b for b in BACKENDS if b.key == key)

    def _populate_models(self):
        backend = self._backend()
        previous = self.model_combo.currentData() or DEFAULT_MODEL
        self.model_combo.clear()
        for name in backend.models():
            label = name
            if isinstance(backend, WhisperCppBackend):
                label += "" if backend.model_path(name).exists() else "  (downloads on first use)"
            self.model_combo.addItem(label, name)
        i = self.model_combo.findData(previous)
        self.model_combo.setCurrentIndex(i if i >= 0 else max(0, self.model_combo.findData(DEFAULT_MODEL)))

    def _set_audio_enabled(self, on: bool):
        for w in self._transport_widgets + [self.boost_btn, self.transcribe_btn]:
            w.setEnabled(on)

    def _set_busy(self, busy: bool):
        self.transcribe_btn.setEnabled(not busy and self.playback_path is not None)
        self.boost_btn.setEnabled(not busy and self.playback_path is not None)
        self.cancel_btn.setEnabled(busy)
        if busy:
            self.sysstats.sample()  # prime CPU% delta
            self.stats_timer.start()
        else:
            self.stats_timer.stop()

    def _mark_dirty(self):
        self.dirty = True
        self._update_title()

    def _update_title(self):
        name = (self.project_path.name if self.project_path
                else self.audio_path.name if self.audio_path else "Untitled")
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} — Transcript Studio")

    def _title_edited(self, text: str):
        if self.session:
            self.session.title = text
            self._mark_dirty()

    def _confirm_discard(self) -> bool:
        if not self.dirty or not self.session:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the current transcript first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def closeEvent(self, e):
        if self._confirm_discard():
            shutil.rmtree(self._audition_dir, ignore_errors=True)
            if self.worker:
                self.worker.cancel.set()
                self.worker.wait(5000)
            e.accept()
        else:
            e.ignore()

    # ------------------------------------------------------------ playback

    def load_audio(self, path: Path, keep_session=False):
        """Open `path`: convert it to the FLAC playback copy in the background, then play that."""
        if audio_mod.is_derived(path) and (orig := audio_mod.original_of(path)):
            log.info("Opened derived copy %s; using its original %s", path.name, orig.name)
            self.statusBar().showMessage(f"{path.name} is the app's own copy — opened the original {orig.name}")
            path = orig
        self._stop_audition()
        self.audio_path, self.playback_path, self.boosted_path = path, None, None
        self.player.stop()
        self.player.setSource(QUrl())
        self.preview_check.setChecked(False)
        self.preview_check.setEnabled(False)
        self.use_boost_check.setChecked(False)
        self.use_boost_check.setEnabled(False)
        self._set_audio_enabled(False)
        if not keep_session:
            # A project exists from the moment a file is opened, so a cleanup plan
            # can be saved before (or without) transcribing.
            self.session = Session(audio_path=str(Path(path).resolve()), model_size="", title=Path(path).stem)
            self.project_path, self.dirty, self.editing = None, False, None
            self.view.set_session(self.session)
            self.wave.view.set_session(self.session)
            self.title_edit.setText(Path(path).stem)
            self._refresh_plan()
        self._update_title()

        def ready(result):
            playback, boosted = result
            if self.audio_path != path:
                return  # another file was opened meanwhile
            self.playback_path = playback
            self._src_offset, self._on_clip = 0.0, False
            self.player.setSource(QUrl.fromLocalFile(str(playback)))
            try:
                self.cleanup.set_source_channels(audio_mod.channels(path))
            except RuntimeError:
                pass
            self.wave.view.set_envelope(self.envelopes.get(playback))
            self.wave.view.set_session(self.session)
            self.cleanup.set_duration(self.wave.view.duration)
            self._set_audio_enabled(True)
            if boosted is not None:
                self._boost_ready(boosted)  # boosted earlier — offer it again
            self.statusBar().showMessage(f"Loaded {path} (playing {playback.name})")

        def job(emit, cancel):
            rep = Reporter(emit, ["Converting to FLAC playback copy", "Drawing waveform"])
            rep.step("Converting to FLAC playback copy")
            playback = audio_mod.make_playback_copy(path, rep, cancel)
            rep.step("Drawing waveform")
            self.envelopes[playback] = compute_envelope(playback, rep, cancel)
            boosted = audio_mod.boosted_path(path)
            if not audio_mod.is_derived_from(boosted, playback):
                return playback, None
            self.envelopes[boosted] = compute_envelope(boosted, rep, cancel)
            return playback, boosted

        log.info("Open audio: %s", path)
        self.envelopes.clear()
        self.wave.view.set_selection(None)
        self.wave.view.set_envelope(None)
        self._start(job, ready, "Preparing audio")

    def _playback_error(self, _err, msg: str):
        log.error("Playback error: %s (source %s)", msg, self.player.source().toLocalFile())
        self.statusBar().showMessage(f"Playback error: {msg}")

    def _busy(self) -> bool:
        if self.worker:
            self.statusBar().showMessage("Busy — wait for the current job to finish (or cancel it).")
        return self.worker is not None

    def open_audio(self):
        if self._busy() or not self._confirm_discard():
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Open audio", str(self.audio_path.parent if self.audio_path else Path.home()), AUDIO_FILTER)
        if fn:
            self.load_audio(Path(fn))

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        elif not self.player.source().isEmpty():
            sel = self.wave.selection
            if sel and not (sel[0] <= self._pos() < sel[1] - 0.05):
                self.seek(sel[0])  # selection = where playback begins
            self._last_pos = None
            self.player.play()

    def _select_sentence(self, sel: tuple[float, float]):
        """Right-click → select the sentence, centered and zoomed to 2/3 of the waveform width."""
        self.wave.view.frame(*sel)
        self.wave.view.set_selection(sel)

    def _center_waveform(self, t: float):
        """Right-click → center the waveform *view* on t (playback position unchanged)."""
        self.wave.view.center_on(t)
        self.statusBar().showMessage(f"Waveform centered on {format_timestamp(t)} "
                                     "(playback position unchanged)")

    def _selection_changed(self, sel):
        log.info("Waveform selection: %s", "cleared" if sel is None else f"{sel[0]:.2f}-{sel[1]:.2f}s")
        self.view.set_audio_selection(sel)
        self.cleanup.set_range(sel)
        if self.cleanup.audition_btn.isChecked():
            if sel is None:
                self.cleanup.audition_btn.setChecked(False)
            else:
                self._audition_timer.start()  # audition the new range
        if sel and self.player.playbackState() == QMediaPlayer.PlayingState:
            self._set_player_pos(sel[0])
            self._last_pos = None

    # ---- position helpers: the only places that convert recording <-> player time

    def _pos(self) -> float:
        """Current playback position in recording time (seconds)."""
        if self._pending_pos is not None:
            return self._pending_pos  # new media still loading: the player reports 0 until then
        return self.player.position() / 1000 + self._src_offset

    def _set_player_pos(self, seconds: float):
        self.player.setPosition(int(max(0.0, seconds - self._src_offset) * 1000))

    def _set_source(self, path: Path, offset: float):
        """Switch the player to `path` (which starts at recording time `offset`), keeping place and play state."""
        here, playing = self._pos(), self.player.playbackState() == QMediaPlayer.PlayingState
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self._src_offset = offset
        self._pending_pos = here
        self._set_player_pos(here)
        if playing:
            self.player.play()  # Qt starts it as soon as the new media has loaded

    def _media_status(self, status):
        if self._pending_pos is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            here, self._pending_pos = self._pending_pos, None
            self._set_player_pos(here)

    def skip(self, seconds: float):
        self.seek(self._pos() + seconds)

    def seek(self, seconds: float):
        full = self._full_ms / 1000
        seconds = max(0.0, min(seconds, full if full > 0 else seconds))
        a = self._audition
        if self._on_clip and a and not (a["start"] <= seconds < a["start"] + a["length"]):
            self.cleanup.audition_btn.setChecked(False)  # jumped out of the auditioned range
        if self._pending_pos is not None:
            self._pending_pos = seconds  # newer than whatever was pending
        self._set_player_pos(seconds)

    def _jump(self):
        t = parse_time(self.jump_edit.text())
        if t is None:
            self.statusBar().showMessage("Jump: use hh:mm:ss, mm:ss or seconds")
            return
        self.seek(t)

    def _slider_released(self):
        self._slider_dragging = False
        self.seek(self.slider.value() / 1000)

    def _duration_changed(self, ms: int):
        if not self._on_clip and ms > 0:  # the audition clip is not the recording's length
            self._full_ms = ms
            self.slider.setRange(0, ms)

    def _state_changed(self, state):
        icon = QStyle.SP_MediaPause if state == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def _tick(self):
        pos = self._pos()
        if not self._slider_dragging:
            self.slider.blockSignals(True)
            self.slider.setValue(int(pos * 1000))
            self.slider.blockSignals(False)
        self.pos_label.setText(f"{format_timestamp(pos)} / {format_timestamp(self._full_ms / 1000)}")
        self.view.highlight_at(pos)
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.wave.view.set_playhead(pos, follow=playing and self.follow_check.isChecked())
        if playing:
            action = selection_action(pos, self._last_pos, self.wave.selection, self.wave.loop)
            if action:
                self._set_player_pos(self.wave.selection[0])
                if action == "stop":
                    self.player.pause()
                pos = None
        self._last_pos = pos if playing else None

    def _normal_source(self) -> Path | None:
        """The full-length file to play: processed if 'Play processed' is ticked, else the original."""
        if self.preview_check.isChecked() and self.boosted_path:
            return self.boosted_path
        return self.playback_path

    def _switch_source(self, _processed: bool = False):
        """'Play processed' toggled: swap original <-> processed, keeping position and play state."""
        if self.cleanup.audition_btn.isChecked():
            self.cleanup.audition_btn.setChecked(False)
        path = self._normal_source()
        if path is None:
            return
        self.wave.view.set_envelope(self.envelopes.get(path))
        self._on_clip = False
        self._set_source(path, 0.0)

    # ------------------------------------------------------------ speech cleanup

    def _cleanup_changed(self):
        self.settings.setValue("cleanup_settings", json.dumps(self.cleanup.settings().to_dict()))
        if self.cleanup.audition_btn.isChecked():
            self._audition_timer.start()  # re-render the audition with the new settings

    # ---- cleanup plan (edit list of blocks + whole-file base)

    def _plan(self) -> dict:
        return self.session.cleanup_plan if self.session else {"whole": None, "blocks": []}

    def _plan_note(self) -> str:
        if not self.session:
            return ""
        plan, rendered = self._plan(), self.session.cleanup_rendered
        empty = not plan.get("whole") and not plan.get("blocks")
        if self.boosted_path and rendered is not None and rendered == plan:
            return "<span style='color:#059669'>✓ Rendered audio is up to date with this plan.</span>"
        if self.boosted_path and rendered is not None:
            return "<span style='color:#d97706'>Plan changed since the last render — Render again.</span>"
        return "" if empty else "Not rendered yet — press Render cleaned audio."

    def _refresh_plan(self):
        plan = self._plan()
        blocks = [(s, e) for s, e, _ in audio_mod.plan_blocks(plan)]
        self.wave.view.set_blocks(blocks, self.editing if isinstance(self.editing, int) else None)
        self.cleanup.set_plan(plan, self.editing, self._plan_note())

    def _edit_plan_entry(self, key):
        """Plan list / waveform band clicked: load that entry's settings (and range) for editing."""
        plan = self._plan()
        if key == WHOLE and plan.get("whole"):
            self.editing = WHOLE
            self.cleanup.mode_whole.setChecked(True)
            self.cleanup.set_settings(audio_mod.CleanupSettings.from_dict(plan["whole"]))
            self.cleanup.status.setText("Editing the whole-file settings. Change them, then Update.")
        elif isinstance(key, int) and 0 <= key < len(plan.get("blocks", [])):
            start, end, settings = audio_mod.plan_blocks(plan)[key]
            self.editing = key
            self.cleanup.mode_range.setChecked(True)
            self.cleanup.set_settings(settings)
            self.wave.view.frame(start, end)
            self.wave.view.set_selection((start, end))
            self.cleanup.status.setText(f"Editing block {key + 1}: adjust its settings, or its range by "
                                        "dragging the selection edges or typing Start/End, then Update.")
        self.tabs.setCurrentIndex(0)
        self._refresh_plan()

    def new_plan_entry(self):
        self.editing = None
        self.cleanup.mode_range.setChecked(True)
        self._refresh_plan()
        self.cleanup.status.setText("New block: select a range on the waveform, choose settings, Save to plan.")

    def save_plan_entry(self):
        if not self.session:
            return
        plan = self._plan()
        settings = self.cleanup.settings().to_dict()
        if self.cleanup.mode_whole.isChecked():
            plan["whole"] = settings
            self.editing = WHOLE
            msg = "Whole-file settings saved to the plan."
        else:
            sel = self.wave.selection
            if not sel or sel[1] - sel[0] < 0.05:
                self.cleanup.status.setText("Select a range on the waveform first (or choose Apply to: Whole file).")
                return
            blocks = plan.setdefault("blocks", [])
            for i, b in enumerate(blocks):
                if i != self.editing and b["start"] < sel[1] and sel[0] < b["end"]:
                    self.cleanup.status.setText(
                        f"<span style='color:#dc2626'>That range overlaps block {i + 1} "
                        f"({fmt_t(b['start'])}–{fmt_t(b['end'])}). Adjust the range, or edit block {i + 1}.</span>")
                    return
            entry = {"start": round(sel[0], 3), "end": round(sel[1], 3), "settings": settings}
            if isinstance(self.editing, int) and self.editing < len(blocks):
                blocks[self.editing] = entry
            else:
                blocks.append(entry)
            blocks.sort(key=lambda b: b["start"])
            self.editing = blocks.index(entry)
            msg = f"Block {self.editing + 1} saved ({fmt_t(sel[0])}–{fmt_t(sel[1])})."
        log.info("Cleanup plan: %s", audio_mod.describe_plan(plan))
        self._mark_dirty()
        self._refresh_plan()
        self.cleanup.status.setText(msg + " Save the project to keep it; Render to apply it.")

    def delete_plan_entry(self):
        plan = self._plan()
        if self.editing == WHOLE:
            plan["whole"] = None
            msg = "Whole-file settings removed from the plan."
        elif isinstance(self.editing, int) and self.editing < len(plan.get("blocks", [])):
            del plan["blocks"][self.editing]
            msg = f"Block {self.editing + 1} deleted."
        else:
            return
        self.editing = None
        log.info("Cleanup plan: %s", audio_mod.describe_plan(plan))
        self._mark_dirty()
        self._refresh_plan()
        self.cleanup.status.setText(msg)

    def render_plan(self):
        """Render the whole plan into the processed ("rendered") audio file."""
        if not self.audio_path or not self.playback_path or self._busy():
            return
        plan = copy.deepcopy(self._plan())
        if not plan.get("whole") and not plan.get("blocks"):
            self.cleanup.status.setText("The plan is empty — save some cleanup settings to it first.")
            return
        src, dst = self.audio_path, audio_mod.boosted_path(self.audio_path)

        def job(emit, cancel):
            rep = Reporter(emit, ["Rendering cleaned audio", "Drawing waveform"])
            rep.step("Rendering cleaned audio")
            out = audio_mod.render_plan(src, dst, plan, rep, cancel)
            rep.step("Drawing waveform")
            self.envelopes[out] = compute_envelope(out, rep, cancel)
            return out, plan

        self.boost_description = audio_mod.describe_plan(plan)
        log.info("Render cleaned audio: %s -> %s", self.boost_description, dst)
        self._start(job, self._plan_rendered, "Speech cleanup")

    def _plan_rendered(self, result):
        path, plan = result
        if self.session:
            self.session.cleanup_rendered = plan
            self._mark_dirty()
        self._boost_ready(path)

    def _boost_ready(self, path: Path):
        self.boosted_path = Path(path)
        self.preview_check.setEnabled(True)
        self.use_boost_check.setEnabled(True)
        self.use_boost_check.setChecked(True)
        self.cleanup.saveas_btn.setEnabled(True)
        if self.session and self.session.cleanup_rendered:
            self.boost_description = audio_mod.describe_plan(self.session.cleanup_rendered)
        if self.preview_check.isChecked():
            self._switch_source(True)  # refresh with the newly written file
        self._refresh_plan()
        self.statusBar().showMessage(f"Rendered audio: {path}")

    def save_rendered_as_recording(self):
        """Copy the rendered audio to a normal file and open it as a new recording (another pass)."""
        if not self.boosted_path or not self.boosted_path.exists() or self._busy():
            return
        if not self._confirm_discard():
            return
        default = self.audio_path.with_name(self.audio_path.stem + "-cleaned.flac")
        fn, _ = QFileDialog.getSaveFileName(self, "Save rendered audio as a new recording", str(default),
                                            "FLAC audio (*.flac)")
        if not fn:
            return
        shutil.copyfile(self.boosted_path, fn)
        log.info("Saved rendered audio as new recording %s (plan: %s)", fn, self.boost_description)
        self.load_audio(Path(fn))
        self.statusBar().showMessage(f"Opened {Path(fn).name} — a fresh recording you can clean up further")

    # ---- selection audition (A/B)

    def _audition_toggled(self, on: bool):
        if on:
            if not self.wave.selection or not self.audio_path:
                self.cleanup.audition_btn.blockSignals(True)
                self.cleanup.audition_btn.setChecked(False)
                self.cleanup.audition_btn.blockSignals(False)
                self.cleanup.status.setText("Select a range on the waveform first (drag across it), "
                                            "then Audition to hear just that part, looping.")
                return
            self.wave.loop_check.setChecked(True)  # audition = listen to the range repeatedly
            self._render_audition(play=True)
        else:
            self._audition_timer.stop()
            if self._on_clip:
                self._on_clip = False
                self._set_source(self._normal_source(), 0.0)
            if self.wave.selection:
                self.cleanup.status.setText("A/B: hearing the ORIGINAL — Ctrl+B (or the button) for processed")
            else:
                self.cleanup.status.setText("")

    def _stop_audition(self):
        if self.cleanup.audition_btn.isChecked():
            self.cleanup.audition_btn.setChecked(False)
        self._audition = None
        self._audition_seq += 1  # ignore any render still in flight

    def _render_audition(self, play: bool):
        sel, src = self.wave.selection, self.audio_path
        if not sel or not src or not self.cleanup.audition_btn.isChecked():
            return
        settings = self.cleanup.settings()
        key = (sel, settings)
        a = self._audition
        if a and a["key"] == key and a["clip"].exists():
            self._use_clip(play)
            return
        full = self._full_ms / 1000 or self.wave.view.duration
        start = max(0.0, sel[0] - 1.0)  # a little context so filters settle before the selection
        length = min(full, sel[1] + 0.5) - start
        self._audition_seq += 1
        seq = self._audition_seq
        dst = self._audition_dir / f"clip{seq}.flac"
        self.cleanup.status.setText("Rendering audition…")
        t0 = time.monotonic()
        w = Worker(lambda emit, cancel: audio_mod.render_clip(src, dst, settings, start, length, cancel),
                   "Audition")
        w.succeeded.connect(lambda path: self._audition_rendered(
            seq, Path(path), start, length, key, play, time.monotonic() - t0))
        w.failed.connect(lambda step, msg: self.cleanup.status.setText(f"Audition failed: {msg}"))
        self._audition_worker = w  # keep a reference while it runs
        w.start()

    def _audition_rendered(self, seq, clip: Path, start, length, key, play, took):
        if seq != self._audition_seq or not self.cleanup.audition_btn.isChecked():
            clip.unlink(missing_ok=True)  # superseded
            return
        old = self._audition["clip"] if self._audition else None
        self._audition = {"clip": clip, "start": start, "length": length, "key": key}
        self._use_clip(play)
        if old and old != clip:
            QTimer.singleShot(2000, lambda: old.unlink(missing_ok=True))  # after the player lets go
        log.info("Audition rendered in %.2fs: %.1f-%.1fs, %s", took, start, start + length, key[1].describe())

    def _use_clip(self, play: bool):
        a = self._audition
        sel = self.wave.selection
        self._on_clip = True
        self._set_source(a["clip"], a["start"])
        if play and self.player.playbackState() != QMediaPlayer.PlayingState:
            self._pending_pos = sel[0]
            self._set_player_pos(sel[0])
            self._last_pos = None
            self.player.play()
        self.cleanup.status.setText(
            f"A/B: hearing PROCESSED {format_timestamp(sel[0])}–{format_timestamp(sel[1])} "
            f"({a['key'][1].describe()}) — Ctrl+B for original")

    # ------------------------------------------------------------ transcription

    def _start(self, fn, on_success, title: str, engine: str = ""):
        w = self.worker = Worker(fn, title)
        w.event.connect(self._on_event)
        w.succeeded.connect(lambda result: self._job_succeeded(w, result, on_success))
        w.failed.connect(self._job_failed)
        w.finished.connect(lambda: self._worker_done(w))
        self._job = {"title": title, "engine": engine, "t0": time.monotonic(), "event": None, "info": {}}
        self.step_label.setText(title)
        self.step_label.setStyleSheet("font-weight: bold;")
        self.detail_label.setText("")
        self._set_busy(True)
        w.start()

    def _worker_done(self, w: Worker):
        if self.worker is not w:
            return
        self.worker = None
        self._set_busy(False)

    def _on_event(self, e: ProgressEvent):
        self._job["event"], self._job["t_event"] = e, time.monotonic()
        self._job["info"].update(e.info)
        steps = f"Step {e.step} of {e.steps} · " if e.steps > 1 else ""
        self.step_label.setText(f"{steps}{e.name}")
        if e.fraction is None:
            self.progress.setRange(0, 0)  # animated "busy" bar: length unknown, not stuck
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(e.fraction * 1000))
        if e.detail:
            self.detail_label.setText(e.detail)
        self._render_stats()

    def _tick_stats(self):
        self._sys = self.sysstats.sample()
        self._render_stats()

    def _render_stats(self):
        job, e = self._job, self._job.get("event")
        if not job:
            return
        now = time.monotonic()
        lines = []
        total = now - job["t0"]
        running = self.worker is not None
        if not running:
            total = job.get("t_done", now) - job["t0"]
        line = f"{'Elapsed' if running else 'Took'} {fmt_duration(total)}"
        if e is not None and running:
            step_elapsed = e.step_elapsed + (now - job["t_event"])
            line += f" · this step {fmt_duration(step_elapsed)}"
            f = e.fraction
            if f and 0.01 < f < 1 and step_elapsed > 3:
                line += f" · step ETA ~{fmt_duration(step_elapsed * (1 - f) / f)}"
        lines.append(line)
        info = job.get("info", {})
        if "audio_done" in info and "audio_total" in info and e is not None and e.name == "Transcribing":
            speed = info["audio_done"] / max(e.step_elapsed, 1e-6)
            lines.append(f"Audio {format_timestamp(info['audio_done'])} / {format_timestamp(info['audio_total'])}"
                         f" · {speed:.1f}× real time")
        sysparts = []
        sd = self._sys
        if "gpu_percent" in sd:
            sysparts.append(f"GPU {sd['gpu_percent']:.0f}%")
        if "vram_used_gb" in sd:
            sysparts.append(f"VRAM {sd['vram_used_gb']:.1f}/{sd['vram_total_gb']:.1f} GB")
        if "cpu_percent" in sd:
            sysparts.append(f"CPU {sd['cpu_percent']:.0f}%")
        if "ram_used_gb" in sd:
            sysparts.append(f"RAM {sd['ram_used_gb']:.1f}/{sd['ram_total_gb']:.0f} GB")
        if sysparts or job.get("engine"):
            lines.append(" · ".join(([job["engine"]] if job.get("engine") else []) + sysparts))
        if info.get("last_text") and running and e is not None and e.name == "Transcribing":
            t = info["last_text"]
            lines.append(f"“{t[:90]}{'…' if len(t) > 90 else ''}”")
        self.stats_label.setText("\n".join(lines))

    def _job_succeeded(self, w: Worker, result, on_success):
        on_success(result)  # the real work first; the summary below is cosmetic
        self._job["t_done"] = time.monotonic()
        total = self._job["t_done"] - self._job.get("t0", self._job["t_done"])
        info = self._job.get("info", {})
        summary = f"✓ {w.title} finished in {fmt_duration(total)}"
        if info.get("audio_total"):
            summary += f" — {info['audio_total'] / max(total, 1e-6):.1f}× real time overall"
        self.step_label.setText(summary)
        self.step_label.setStyleSheet("font-weight: bold; color: #059669;")
        self.detail_label.setText("")
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        self._render_stats()

    def _job_failed(self, step: str, msg: str):
        self._job["t_done"] = time.monotonic()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        cancelled = msg == "Cancelled."
        self.step_label.setText(f"{'Cancelled' if cancelled else '✗ Failed'} during: {step}")
        self.step_label.setStyleSheet(f"font-weight: bold; color: {'#6b7280' if cancelled else '#dc2626'};")
        self.detail_label.setText("" if cancelled else msg[:300])
        if cancelled:
            return
        box = QMessageBox(QMessageBox.Warning, "Transcript Studio", f"Failed during: {step}\n\n{msg}", parent=self)
        path = logs.current_log_file()
        if path:
            box.setInformativeText(f"Full details are in the log:\n{path}")
            open_btn = box.addButton("Open log", QMessageBox.ActionRole)
        else:
            box.setInformativeText("Turn on “Write log file” to capture full details next time.")
            open_btn = None
        box.addButton(QMessageBox.Close)
        box.exec()
        if open_btn is not None and box.clickedButton() is open_btn:
            self.view_log()

    # ------------------------------------------------------------ logging

    def _set_logging(self, on: bool):
        path = logs.set_file_logging(on)
        self.settings.setValue("log_to_file", on)
        for w in (self.log_check, self.log_act):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        self.statusBar().showMessage(f"Logging to {path}" if on else "Log file off")

    def view_log(self):
        path = logs.current_log_file()
        if path is None:
            logs_ = sorted(logs.LOG_DIR.glob("transcript-studio-*.log"))
            if not logs_:
                self.statusBar().showMessage("No log files yet — turn on “Write log file”.")
                return
            path = logs_[-1]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_log_folder(self):
        logs.LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs.LOG_DIR)))

    def transcribe(self):
        if not self.playback_path or self._busy():
            return
        if self.session and self.session.paragraphs and not self._confirm_discard():
            return
        self.tabs.setCurrentWidget(self.transcribe_box)
        backend = self._backend()
        opts = PipelineOptions(
            model_size=self.model_combo.currentData(), backend=backend,
            num_speakers=self.speakers_spin.value() or None,
            low_confidence_threshold=self.threshold_spin.value(),
            speaker_model=self.speaker_model_combo.currentData(),
            boost_description=self.boost_description,
        )
        audio_path = self.audio_path
        boosted = self.boosted_path if self.use_boost_check.isChecked() else None

        def job(emit, cancel):
            return run_pipeline(audio_path, opts, emit, cancel, boosted_audio_path=boosted)

        self.statusBar().showMessage(f"Transcribing with {backend.label}, model {opts.model_size}…")
        self._start(job, self._transcribed, "Transcription", f"{backend.label} · {opts.model_size}")

    def _transcribed(self, session: Session):
        session.title = self.title_edit.text() or session.title
        if self.session is not None:  # the cleanup plan and sound tags belong to the recording
            session.cleanup_plan = self.session.cleanup_plan
            session.cleanup_rendered = self.session.cleanup_rendered
            session.sound_tags = self.session.sound_tags
        if session.sound_tags:
            log.info("Inserted %d sound tag(s) into the new transcript", sounds.apply_all(session))
        keep_project = self.project_path
        self._show_session(session)
        self.project_path = keep_project
        self.dirty = True  # new, unsaved result
        self._update_title()
        speakers = len(session.speaker_ids())
        self.statusBar().showMessage(
            f"Transcribed: {len(session.paragraphs)} paragraphs, {speakers} speaker(s) "
            f"[{getattr(session, 'diarization_detail', '')}]")

    def name_participants(self):
        if not self.session or not self.session.speaker_ids():
            self.statusBar().showMessage("Transcribe or open a project first — there are no participants yet.")
            return
        dlg = ParticipantsDialog(self.session, self)
        if dlg.exec() != ParticipantsDialog.Accepted:
            return
        names = dlg.names()
        if names != self.session.speaker_names:
            self.apply_speaker_names(names)

    def apply_speaker_names(self, names: dict[str, str]):
        self.session.speaker_names = names
        log.info("Participant names: %s", names or "(none)")
        self.view.relabel_speakers()
        self._mark_dirty()
        self.statusBar().showMessage("Participant names updated" if names else "Participant names cleared")

    # ------------------------------------------------------------ help

    def show_manual(self):
        if getattr(self, "_manual", None) is None:
            self._manual = ManualWindow(self)
        self._manual.show()
        self._manual.raise_()
        self._manual.activateWindow()

    # ------------------------------------------------------------ sound tags

    def tag_sound(self):
        """Label the selected stretch of audio as a sound (sneeze, horn, bell…)."""
        sel = self.wave.selection
        if not sel or not self.session:
            return
        label, ok = QInputDialog.getItem(
            self, "Tag a sound", f"What is the sound at {format_timestamp(sel[0])}?\n"
            "(type your own word, or pick one)", sounds.SUGGESTIONS, 0, True)
        label = label.strip().strip("()[]").strip() if ok else ""
        if not label:
            return
        tag = {"start": round(sel[0], 3), "end": round(sel[1], 3), "label": label}
        self.session.sound_tags.append(tag)
        log.info("Sound tag added: %s", tag)
        if self.session.paragraphs:
            hit = sounds.insert_tag(self.session, tag["start"], tag["end"], label)
            self._refresh_transcript()
            msg = (f"Tagged ({label}) — inserted in sentence {hit[0].index}.{hit[1].index}" if hit
                   else f"Tagged ({label})")
        else:
            msg = f"Tagged ({label}) — it will be inserted into the transcript when you transcribe"
        self.wave.view.set_session(self.session)
        self._mark_dirty()
        self.statusBar().showMessage(msg)

    def remove_sound_tag(self, index: int):
        if not self.session or not 0 <= index < len(self.session.sound_tags):
            return
        label = self.session.sound_tags[index]["label"]
        removed_text = sounds.remove_tag(self.session, index)
        if removed_text:
            self._refresh_transcript()
        self.wave.view.set_session(self.session)
        self._mark_dirty()
        self.statusBar().showMessage(f"Removed sound tag ({label})"
                                     + (" and its text in the transcript" if removed_text else ""))

    def _refresh_transcript(self):
        """Rebuild the transcript view after a structural text change, keeping the scroll position."""
        bar = self.view.verticalScrollBar()
        pos = bar.value()
        self.view.set_session(self.session)
        self.view.set_audio_selection(self.wave.selection, scroll=False)
        QTimer.singleShot(0, lambda: bar.setValue(pos))

    def _show_session(self, session: Session):
        self.session = session
        self.editing = None
        self._refresh_plan()
        self.title_edit.setText(session.title)
        self.view.set_session(session)
        self.wave.view.set_session(session)
        self.view.set_audio_selection(self.wave.selection, scroll=False)

    # ------------------------------------------------------------ project / export

    def _default_project_path(self) -> Path:
        base = Path(self.session.audio_path)
        return base.with_name(base.stem + ".tsproj.json")

    def save_project(self) -> bool:
        if not self.session:
            return False
        if not self.project_path:
            return self.save_project_as()
        self.session.save(self.project_path)
        log.info("Saved project: %s", self.project_path)
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {self.project_path}")
        return True

    def save_project_as(self) -> bool:
        if not self.session:
            return False
        fn, _ = QFileDialog.getSaveFileName(self, "Save project", str(self.project_path or self._default_project_path()), PROJECT_FILTER)
        if not fn:
            return False
        self.project_path = Path(fn)
        return self.save_project()

    def open_project(self):
        if self._busy() or not self._confirm_discard():
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Open project", str(Path.home()), PROJECT_FILTER + ";;All files (*)")
        if fn:
            self.load_project(Path(fn))

    def load_project(self, path: Path):
        log.info("Open project: %s", path)
        try:
            session = Session.load(path)
        except Exception as e:
            log.exception("Could not read project %s", path)
            QMessageBox.warning(self, "Open project", f"Could not read {path}:\n{e}")
            return
        audio = Path(session.audio_path)
        if not audio.exists() and (path.parent / audio.name).exists():
            audio = path.parent / audio.name  # project moved together with its audio
            session.audio_path = str(audio)
        if audio.exists():
            self.load_audio(audio, keep_session=True)  # also re-offers the boosted copy
        else:
            QMessageBox.information(self, "Open project", f"Audio file not found:\n{audio}\n\n"
                                    "The transcript is loaded; use Open Audio to attach the recording.")
        self.project_path = path
        self._show_session(session)
        self.dirty = False
        self._update_title()

    def export_transcript(self):
        """File → Export…: Markdown, PDF or Word; details header on/off; any location."""
        if not self.session or not self.session.paragraphs:
            QMessageBox.information(self, "Export", "Nothing to export yet — transcribe the recording first.")
            return
        audio = Path(self.session.audio_path)
        folder = Path(self.settings.value("export_dir", "", type=str) or audio.parent)
        if not folder.is_dir():
            folder = audio.parent
        dlg = ExportDialog(folder, audio.stem, self.settings.value("export_format", "md", type=str),
                           self.settings.value("export_details", True, type=bool), self)
        if dlg.exec() != ExportDialog.Accepted:
            return
        path, fmt, details = dlg.path(), dlg.fmt(), dlg.include_details()
        try:
            export_formats.export(self.session, path, fmt, details)
        except Exception as e:
            log.exception("Export to %s failed", path)
            QMessageBox.warning(self, "Export", f"Could not export {path.name}:\n{e}")
            return
        self.settings.setValue("export_dir", str(path.parent))
        self.settings.setValue("export_format", fmt)
        self.settings.setValue("export_details", details)
        log.info("Exported %s (%s, details=%s)", path, fmt, details)
        self.statusBar().showMessage(f"Exported {path}")
