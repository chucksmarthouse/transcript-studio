"""Speech cleanup tab: filter controls, an edit plan of cleanup blocks, audition (A/B), render.

Left: what to apply (whole file / selected range, with numeric start & end),
the filter controls, Audition and Save-to-plan. Right: the plan (whole-file
base settings + numbered blocks) with Render and what to do with the result.
The main window owns the plan data; this widget only displays it and emits
requests.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..audio import CHANNELS, DENOISERS, LEVELERS, PRESETS, CleanupSettings, plan_blocks, plan_whole

CUSTOM = "Custom"
WHOLE = "whole"  # plan-list key for the whole-file entry (blocks use their int index)


def _combo(items: dict[str, str]) -> QComboBox:
    c = QComboBox()
    for key, label in items.items():
        c.addItem(label, key)
    return c


def _hz_spin(maximum: int, step: int, tip: str) -> QSpinBox:
    s = QSpinBox()
    s.setRange(0, maximum)
    s.setSingleStep(step)
    s.setSuffix(" Hz")
    s.setSpecialValueText("off")
    s.setToolTip(tip)
    return s


def fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m) // 60}:{int(m) % 60:02d}:{s:05.2f}" if m >= 60 else f"{int(m):02d}:{s:05.2f}"


class CleanupPanel(QWidget):
    settingsChanged = Signal()
    rangeEdited = Signal(float, float)  # start/end typed into the spin boxes
    saveRequested = Signal()
    newRequested = Signal()
    deleteRequested = Signal()
    planItemSelected = Signal(object)  # WHOLE or block index
    renderRequested = Signal()
    saveAsRecordingRequested = Signal()

    def __init__(self):
        super().__init__()
        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        outer.addLayout(left, 3)

        # ---- what to apply it to
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Apply to</b>"))
        self.mode_range = QRadioButton("Selected range")
        self.mode_range.setToolTip("Save these settings as a cleanup block for the selected range")
        self.mode_whole = QRadioButton("Whole file")
        self.mode_whole.setToolTip("Save these settings as the base for the whole recording\n"
                                   "(blocks override it inside their ranges)")
        self.mode_range.setChecked(True)
        modes = QButtonGroup(self)
        modes.addButton(self.mode_range)
        modes.addButton(self.mode_whole)
        row.addWidget(self.mode_range)
        row.addWidget(self.mode_whole)
        row.addSpacing(16)
        row.addWidget(QLabel("Start"))
        self.start_spin = self._time_spin()
        row.addWidget(self.start_spin)
        row.addWidget(QLabel("End"))
        self.end_spin = self._time_spin()
        row.addWidget(self.end_spin)
        self.range_label = QLabel("")
        self.range_label.setStyleSheet("color: #6b7280;")
        row.addWidget(self.range_label)
        row.addStretch(1)
        left.addLayout(row)

        # ---- filters
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        self.preset = QComboBox()
        self.preset.addItems([*PRESETS, CUSTOM])
        self.preset.setToolTip("Starting points — tweak any control afterwards")
        self.channel = _combo(CHANNELS)
        self.channel.setToolTip("Stereo files: use one channel if it's clearer, or left − right to cancel\n"
                                "noise that's equal in both. Mono files ignore this.")
        g.addWidget(QLabel("Preset"), 0, 0)
        g.addWidget(self.preset, 0, 1, 1, 3)
        g.addWidget(QLabel("Channel"), 0, 4)
        g.addWidget(self.channel, 0, 5)

        self.highpass = _hz_spin(500, 10, "Low cut (high-pass): removes rumble, handling noise, wind, HVAC.\n"
                                          "Speech: 80–120 Hz; up to 200 Hz for heavy rumble.")
        self.lowpass = _hz_spin(16000, 250, "High cut (low-pass): removes hiss and whine.\n"
                                            "6–8 kHz is gentle; 4 kHz is 'telephone'.")
        self.hum = _combo({"0": "Off", "50": "50 Hz", "60": "60 Hz"})
        self.hum.setToolTip("Notch out mains hum and its harmonics (60 Hz in North America)")
        g.addWidget(QLabel("Low cut"), 1, 0)
        g.addWidget(self.highpass, 1, 1)
        g.addWidget(QLabel("High cut"), 1, 2)
        g.addWidget(self.lowpass, 1, 3)
        g.addWidget(QLabel("Hum notch"), 1, 4)
        g.addWidget(self.hum, 1, 5)

        self.denoise = _combo(DENOISERS)
        self.denoise.setToolTip("FFT: steady hiss/fan noise · Wavelet: hiss, keeps consonants crisp\n"
                                "RNNoise: neural, best for changing noise (traffic, chatter, keyboards)\n"
                                "NL-means: smooth broadband noise, slow (~6 min per hour of audio)")
        self.strength = _combo({"light": "Light", "medium": "Medium", "strong": "Strong"})
        self.presence = QDoubleSpinBox()
        self.presence.setRange(0, 12)
        self.presence.setSingleStep(1)
        self.presence.setSuffix(" dB")
        self.presence.setSpecialValueText("off")
        self.presence.setToolTip("Boost around 3 kHz: makes muffled or distant voices clearer")
        g.addWidget(QLabel("Noise reduction"), 2, 0)
        g.addWidget(self.denoise, 2, 1)
        g.addWidget(QLabel("Strength"), 2, 2)
        g.addWidget(self.strength, 2, 3)
        g.addWidget(QLabel("Presence"), 2, 4)
        g.addWidget(self.presence, 2, 5)

        self.leveler = _combo(LEVELERS)
        self.leveler.setToolTip("Brings quiet speech up towards loud speech — good when one person\n"
                                "is near the recorder and another is across the room")
        self.declick = QCheckBox("De-click")
        self.declick.setToolTip("Remove clicks, pops and crackle")
        self.declip = QCheckBox("De-clip")
        self.declip.setToolTip("Repair distorted (clipped) peaks")
        self.normalize = QCheckBox("Normalize")
        self.normalize.setToolTip("EBU R128 loudness normalisation to -16 LUFS")
        self.gain = QDoubleSpinBox()
        self.gain.setRange(-20, 30)
        self.gain.setSuffix(" dB")
        self.gain.setToolTip("Extra gain after everything else (a limiter prevents clipping)")
        g.addWidget(QLabel("Leveler"), 3, 0)
        g.addWidget(self.leveler, 3, 1)
        rep = QHBoxLayout()
        rep.addWidget(self.declick)
        rep.addWidget(self.declip)
        rep.addWidget(self.normalize)
        g.addLayout(rep, 3, 2, 1, 2)
        g.addWidget(QLabel("Gain"), 3, 4)
        g.addWidget(self.gain, 3, 5)
        left.addLayout(g)

        # ---- audition + save
        row = QHBoxLayout()
        self.audition_btn = QPushButton("▶ Audition selection (A/B)")
        self.audition_btn.setCheckable(True)
        self.audition_btn.setStyleSheet("QPushButton:checked { background: #1d4ed8; color: white; "
                                        "font-weight: bold; }")
        self.audition_btn.toggled.connect(lambda on: self.audition_btn.setText(
            "■ Hearing PROCESSED (A/B)" if on else "▶ Audition selection (A/B)"))
        self.audition_btn.setToolTip("Hear these settings on the selected range only, looping.\n"
                                     "Toggle (or Ctrl+B) to flip between processed and original.\n"
                                     "Changing settings re-renders instantly.")
        self.save_btn = QPushButton("Save to plan")
        self.save_btn.setStyleSheet("font-weight: bold;")
        self.new_btn = QPushButton("New block")
        self.new_btn.setToolTip("Stop editing the current entry and start a new block")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setToolTip("Remove the entry being edited from the plan")
        row.addWidget(self.audition_btn)
        row.addWidget(self.save_btn)
        row.addWidget(self.new_btn)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        left.addLayout(row)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #1d4ed8;")
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        left.addStretch(1)

        # ---- the plan
        right = QVBoxLayout()
        outer.addLayout(right, 2)
        right.addWidget(QLabel("<b>Cleanup plan</b> — click an entry (or a band above the waveform) to edit it"))
        self.plan_list = QListWidget()
        self.plan_list.setMinimumHeight(90)
        right.addWidget(self.plan_list, 1)
        row = QHBoxLayout()
        self.render_btn = QPushButton("Render cleaned audio")
        self.render_btn.setToolTip("Write a new audio file with the whole plan applied:\n"
                                   "whole-file settings everywhere, each block's settings in its range")
        self.render_btn.setStyleSheet("font-weight: bold;")
        self.saveas_btn = QPushButton("Save as new recording…")
        self.saveas_btn.setToolTip("Save the rendered audio under a name of your choice and open it as a\n"
                                   "new recording — e.g. to clean it up further in another pass")
        self.saveas_btn.setEnabled(False)
        row.addWidget(self.render_btn)
        row.addWidget(self.saveas_btn)
        right.addLayout(row)
        row = QHBoxLayout()
        self.preview_check = QCheckBox("Play rendered")
        self.preview_check.setToolTip("Play the rendered file instead of the original (transcript unaffected)")
        self.preview_check.setEnabled(False)
        self.use_check = QCheckBox("Transcribe rendered")
        self.use_check.setToolTip("Transcribe the rendered file. Tip: heavy denoising can sound better\n"
                                  "but transcribe worse — 'Gentle' is a good choice for transcription.")
        self.use_check.setEnabled(False)
        row.addWidget(self.preview_check)
        row.addWidget(self.use_check)
        row.addStretch(1)
        right.addLayout(row)
        self.plan_status = QLabel("")
        self.plan_status.setWordWrap(True)
        right.addWidget(self.plan_status)

        # back-compat name used by the main window's busy handling
        self.apply_btn = self.render_btn

        self._loading = False
        self.preset.currentTextChanged.connect(self._preset_chosen)
        for w in (self.channel, self.hum, self.denoise, self.strength, self.leveler):
            w.currentIndexChanged.connect(self._changed)
        for w in (self.highpass, self.lowpass, self.presence, self.gain):
            w.valueChanged.connect(self._changed)
        for w in (self.declick, self.declip, self.normalize):
            w.toggled.connect(self._changed)
        self.start_spin.valueChanged.connect(self._range_typed)
        self.end_spin.valueChanged.connect(self._range_typed)
        self.save_btn.clicked.connect(self.saveRequested)
        self.new_btn.clicked.connect(self.newRequested)
        self.delete_btn.clicked.connect(self.deleteRequested)
        self.render_btn.clicked.connect(self.renderRequested)
        self.saveas_btn.clicked.connect(self.saveAsRecordingRequested)
        self.plan_list.itemClicked.connect(lambda item: self.planItemSelected.emit(item.data(Qt.UserRole)))
        self.set_settings(CleanupSettings())
        self.set_range(None)
        self.set_plan({"whole": None, "blocks": []}, None)

    @staticmethod
    def _time_spin() -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setDecimals(2)
        s.setSingleStep(0.05)
        s.setSuffix(" s")
        s.setRange(0, 0)
        s.setKeyboardTracking(False)  # apply when the value is committed, not on every keystroke
        s.setToolTip("Seconds from the start of the recording (or drag the selection edges)")
        return s

    # ---- range (mirrors the waveform selection)

    def set_duration(self, seconds: float):
        for s in (self.start_spin, self.end_spin):
            s.blockSignals(True)
            s.setRange(0, max(0.0, seconds))
            s.blockSignals(False)

    def set_range(self, sel: tuple[float, float] | None):
        for s, v in ((self.start_spin, sel[0] if sel else 0.0), (self.end_spin, sel[1] if sel else 0.0)):
            s.blockSignals(True)
            s.setValue(v)
            s.setEnabled(sel is not None)
            s.blockSignals(False)
        self.range_label.setText(f"{fmt_t(sel[0])} – {fmt_t(sel[1])} ({sel[1] - sel[0]:.2f} s)" if sel
                                 else "drag across the waveform to select a range")

    def _range_typed(self, _v):
        a, b = self.start_spin.value(), self.end_spin.value()
        if b > a:
            self.rangeEdited.emit(a, b)

    # ---- plan display

    def set_plan(self, plan: dict, editing, rendered_note: str = ""):
        self.plan_list.clear()
        whole = plan_whole(plan)
        if whole is not None:
            item = QListWidgetItem(f"Whole file — {whole.describe()}")
            item.setToolTip(f"Whole file:\n{whole.describe()}")
            item.setData(Qt.UserRole, WHOLE)
            self.plan_list.addItem(item)
        for i, (s, e, st) in enumerate(plan_blocks(plan)):
            item = QListWidgetItem(f"{i + 1}.  {fmt_t(s)} – {fmt_t(e)}   {st.describe()}")
            item.setToolTip(f"Block {i + 1}: {fmt_t(s)} – {fmt_t(e)} ({e - s:.2f} s)\n{st.describe()}")
            item.setData(Qt.UserRole, i)
            self.plan_list.addItem(item)
        if self.plan_list.count() == 0:
            item = QListWidgetItem("(empty — choose settings, then Save to plan)")
            item.setFlags(Qt.NoItemFlags)
            self.plan_list.addItem(item)
        for r in range(self.plan_list.count()):
            item = self.plan_list.item(r)
            if editing is not None and item.data(Qt.UserRole) == editing:
                self.plan_list.setCurrentItem(item)
        if editing is None:
            self.plan_list.clearSelection()
        if editing == WHOLE:
            self.save_btn.setText("Update whole-file settings")
        elif isinstance(editing, int):
            self.save_btn.setText(f"Update block {editing + 1}")
        else:
            self.save_btn.setText("Save to plan")
        self.delete_btn.setEnabled(editing is not None)
        self.plan_status.setText(rendered_note)

    # ---- settings <-> controls

    def settings(self) -> CleanupSettings:
        return CleanupSettings(
            channel=self.channel.currentData(), highpass_hz=self.highpass.value(),
            lowpass_hz=self.lowpass.value(), hum_hz=int(self.hum.currentData()),
            presence_db=self.presence.value(), declick=self.declick.isChecked(),
            declip=self.declip.isChecked(), denoise=self.denoise.currentData(),
            denoise_strength=self.strength.currentData(), leveler=self.leveler.currentData(),
            normalize=self.normalize.isChecked(), gain_db=self.gain.value())

    def set_settings(self, s: CleanupSettings):
        self._loading = True
        try:
            self.channel.setCurrentIndex(max(0, self.channel.findData(s.channel)))
            self.highpass.setValue(s.highpass_hz)
            self.lowpass.setValue(s.lowpass_hz)
            self.hum.setCurrentIndex(max(0, self.hum.findData(str(s.hum_hz))))
            self.presence.setValue(s.presence_db)
            self.declick.setChecked(s.declick)
            self.declip.setChecked(s.declip)
            self.denoise.setCurrentIndex(max(0, self.denoise.findData(s.denoise)))
            self.strength.setCurrentIndex(max(0, self.strength.findData(s.denoise_strength)))
            self.leveler.setCurrentIndex(max(0, self.leveler.findData(s.leveler)))
            self.normalize.setChecked(s.normalize)
            self.gain.setValue(s.gain_db)
        finally:
            self._loading = False
        self._sync_preset()
        self.strength.setEnabled(s.denoise != "off")
        self.settingsChanged.emit()

    def set_source_channels(self, n: int):
        self.channel.setEnabled(n >= 2)
        if n < 2:
            self.channel.setToolTip("Mono recording — nothing to choose")

    def _preset_chosen(self, name: str):
        if not self._loading and name in PRESETS:
            keep_channel = self.channel.currentData()  # the channel choice is about the file, not the preset
            s = PRESETS[name]
            self.set_settings(CleanupSettings(**{**s.to_dict(), "channel": keep_channel}))

    def _changed(self, *_):
        if self._loading:
            return
        self.strength.setEnabled(self.denoise.currentData() != "off")
        self._sync_preset()
        self.settingsChanged.emit()

    def _sync_preset(self):
        current = self.settings()
        current_no_channel = CleanupSettings(**{**current.to_dict(), "channel": "mix"})
        name = next((n for n, p in PRESETS.items() if p == current_no_channel), CUSTOM)
        self.preset.blockSignals(True)
        self.preset.setCurrentText(name)
        self.preset.blockSignals(False)
