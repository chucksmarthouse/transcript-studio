"""Headless GUI checks for the spec §10 acceptance criteria that don't need a human ear."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.test_export import REFERENCE, ROOT, SENT_RE, HEADER_RE, parse_reference
from transcript_studio.model import Session
from transcript_studio.ui.main_window import MainWindow, parse_time

AUDIO = ROOT / "samples" / "multispeaker" / "nasa-podcast-ep434-2speakers.mp3"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def project(tmp_path):
    """The reference transcript as a project, with sentence times spread through each paragraph."""
    s = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    s.audio_path = str(AUDIO)
    paras = s.paragraphs
    for i, p in enumerate(paras):
        nxt = paras[i + 1].start_time if i + 1 < len(paras) else p.start_time + 10
        step = (nxt - p.start_time) / len(p.sentences)
        for j, sent in enumerate(p.sentences):
            sent.start_time = p.start_time + j * step
            sent.end_time = sent.start_time + step
    path = tmp_path / "ref.tsproj.json"
    s.save(path)
    return path


@pytest.fixture
def win(app, project):
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.load_project(project)
    assert wait_until(app, lambda: w.player.duration() > 0), "player never loaded the audio"
    yield w
    w.dirty = False
    w.close()


def current(w):
    return w.view._current


def test_parse_time():
    assert parse_time("1:02:03") == 3723 and parse_time("2:03") == 123 and parse_time("90.5") == 90.5
    assert parse_time("abc") is None


def test_playback_position_drives_highlight(app, win):
    win.seek(47)  # 00:00:47 -> paragraph 5 (00:45), first sentence
    assert wait_until(app, lambda: current(win) is not None and current(win).para.index == 5)
    assert current(win).highlighted
    first = current(win)
    win.seek(255)  # 00:04:15 -> paragraph 19
    assert wait_until(app, lambda: current(win).para.index == 19)
    assert not first.highlighted, "previous sentence should be un-highlighted"
    # Auto-scroll brought the row into view.
    row = current(win)
    top = row.mapTo(win.view.widget(), QPoint(0, 0)).y()
    scroll = win.view.verticalScrollBar().value()
    assert scroll <= top <= scroll + win.view.viewport().height()


def test_clicking_text_never_seeks(app, win):
    win.seek(200)
    assert wait_until(app, lambda: abs(win.player.position() - 200_000) < 500)
    target = win.view._rows[5].edit
    win.view.ensureWidgetVisible(target)
    for _ in range(3):
        QTest.mouseClick(target.viewport(), Qt.LeftButton, pos=QPoint(10, 5))
        QTest.mouseDClick(target.viewport(), Qt.LeftButton, pos=QPoint(10, 5))
        app.processEvents()
    assert abs(win.player.position() - 200_000) < 500


def test_edit_reassign_export_save_reopen(app, win, tmp_path):
    row = win.view._rows[3]  # paragraph 2, sentence 1
    row.edit.setPlainText("Edited sentence.")
    block = win.view.widget().layout().itemAt(2).widget()  # paragraph 3
    block.speaker.setCurrentIndex(block.speaker.findData(3))
    win.view._rows[8].flag.setChecked(True)  # manual low-confidence toggle
    assert win.dirty

    md_path = tmp_path / "out.md"
    from transcript_studio.export import export_markdown
    export_markdown(win.session, md_path)
    md = md_path.read_text(encoding="utf-8")
    assert '**2.1** <span style="color:#1d4ed8">Edited sentence.</span>' in md
    assert "### 3. 🟠 Participant 3 — _00:00:02_" in md
    assert '- 🟠 <span style="color:#d97706">Participant 3</span>' in md
    assert md.count("🔴") > REFERENCE.read_text(encoding="utf-8").count("🔴")
    body = md.split("---\n", 1)[1].splitlines()
    assert all(HEADER_RE.match(l) or SENT_RE.match(l) for l in body if l)

    win.project_path = tmp_path / "saved.tsproj.json"
    assert win.save_project()
    assert not win.dirty
    w2 = MainWindow()
    w2.load_project(win.project_path)
    assert w2.session == win.session
    assert w2.session.paragraphs[2].speaker_id == 3
    w2.close()


def test_name_participants_updates_dropdowns_and_export(app, win, tmp_path):
    from transcript_studio.ui.participants_dialog import ParticipantsDialog, participant_summary
    summary = participant_summary(win.session)
    assert set(summary) == {1, 2} and summary[1]["first"] == 0
    dlg = ParticipantsDialog(win.session, win)
    dlg.edits[1].setText("Host")
    win.apply_speaker_names(dlg.names())
    block = win.view.widget().layout().itemAt(1).widget()  # paragraph 2 (Participant 1)
    assert block.speaker.currentText().endswith("Host")
    assert win.dirty
    from transcript_studio.export import to_markdown
    assert "### 2. 🔵 Host — _00:00:00_" in to_markdown(win.session, details=False)


def _menu_action(row, starts_with, char_pos=0):
    from PySide6.QtCore import QPoint
    edit = row.edit
    cur = edit.textCursor()
    cur.setPosition(char_pos)
    pos = edit.cursorRect(cur).center()
    menu = row.edit.build_context_menu(pos)
    return next(a for a in menu.actions() if a.text().startswith(starts_with))


def test_right_click_centers_waveform_without_seeking(app, win):
    win.seek(30)
    assert wait_until(app, lambda: abs(win.player.position() - 30_000) < 500)
    win.wave.view.zoom(20)  # ~42 s visible
    row = win.view._rows[20]
    _menu_action(row, "Center waveform here").trigger()
    v = win.wave.view
    center = v.view_start + v.view_span / 2
    assert abs(center - row.sent.start_time) < 1.0
    app.processEvents()
    assert abs(win.player.position() - 30_000) < 500, "centering must not move playback"


def test_right_click_selects_sentence_and_inverts_it(app, win):
    row = win.view._rows[20]
    _menu_action(row, "Select this sentence").trigger()
    assert win.wave.selection == (row.sent.start_time, row.sent.end_time)
    assert row.edit.extraSelections(), "selected sentence should be inverted"
    assert not win.view._rows[5].edit.extraSelections()
    win.wave.view.set_selection(None)
    assert not row.edit.extraSelections(), "clearing the selection removes the inversion"


def test_waveform_selection_scrolls_and_inverts_matching_text(app, win):
    win.view.verticalScrollBar().setValue(0)
    win.wave.view.set_selection((250, 254))  # inside the last paragraph
    app.processEvents()
    marked = [r for r in win.view._rows if r.edit.extraSelections()]
    assert marked and all(r.sent.end_time > 250 and r.sent.start_time < 254 for r in marked)
    assert win.view.verticalScrollBar().value() > 0, "transcript should scroll to the selection"
    es = marked[0].edit.extraSelections()[0]
    assert es.format.foreground().color().name() == "#ffffff"


def test_word_level_inversion_and_click_time(app):
    from transcript_studio.model import Paragraph, Sentence, Word
    from transcript_studio.ui.transcript_view import SentenceRow
    words = [Word("Hello", 10.0, 10.4, 0.9), Word("there,", 10.5, 10.9, 0.9), Word("friend.", 11.0, 11.6, 0.9)]
    para = Paragraph(1, 1, False, 10.0)
    sent = Sentence(1, "Hello there, friend.", words=words, start_time=10.0, end_time=11.6)
    row = SentenceRow(para, sent)
    assert row.time_at_char(0) == 10.0 and row.time_at_char(7) == 10.5 and row.time_at_char(14) == 11.0
    assert row.mark_audio_selection((10.45, 10.95))
    spans = sorted((e.cursor.selectionStart(), e.cursor.selectionEnd()) for e in row.edit.extraSelections())
    assert spans == [(6, 12)]  # only "there,"
    row.edit.setPlainText("Hi friend.")  # edited: word count changed -> proportional fallback
    assert 10.0 <= row.time_at_char(3) <= 11.6


def test_select_sentence_frames_it_at_two_thirds(app, win):
    win.wave.view.fit()
    row = win.view._rows[20]
    s, e = row.sent.start_time, row.sent.end_time
    _menu_action(row, "Select this sentence").trigger()
    v = win.wave.view
    assert v.view_span == pytest.approx((e - s) * 1.5, rel=1e-6)
    assert v.view_start + v.view_span / 2 == pytest.approx((s + e) / 2, abs=1e-6)
    left, right = v.x_at(s) / v.width(), v.x_at(e) / v.width()
    assert left == pytest.approx(1 / 6) and right == pytest.approx(5 / 6)


# ---------------------------------------------------------------- speech cleanup panel

def test_cleanup_panel_presets_and_custom(app):
    from transcript_studio.audio import PRESETS, CleanupSettings
    from transcript_studio.ui.cleanup_panel import CUSTOM, CleanupPanel
    panel = CleanupPanel()
    panel.preset.setCurrentText("Rumble & hiss")
    assert panel.settings() == PRESETS["Rumble & hiss"]
    assert panel.highpass.value() == 120 and panel.denoise.currentData() == "fft"
    panel.highpass.setValue(130)
    assert panel.preset.currentText() == CUSTOM
    panel.highpass.setValue(120)
    assert panel.preset.currentText() == "Rumble & hiss"  # back to an exact preset match
    panel.denoise.setCurrentIndex(panel.denoise.findData("off"))
    assert not panel.strength.isEnabled()
    s = CleanupSettings(channel="left", hum_hz=50, declick=True, gain_db=4)
    panel.set_settings(s)
    assert panel.settings() == s
    panel.set_source_channels(1)
    assert not panel.channel.isEnabled()


def _wait_worker(app, win, attr="_audition_worker", timeout=20):
    assert wait_until(app, lambda: getattr(win, attr) is None or getattr(win, attr).isFinished(), timeout)
    app.processEvents()


def test_audition_needs_a_selection(app, win):
    win.wave.view.set_selection(None)
    win.cleanup.audition_btn.setChecked(True)
    assert not win.cleanup.audition_btn.isChecked()
    assert "Select a range" in win.cleanup.status.text()


def test_audition_plays_processed_clip_in_recording_time(app, win):
    win.cleanup.preset.setCurrentText("Telephone (200 Hz – 4 kHz)")
    win.wave.view.set_selection((100.0, 104.0))
    win.cleanup.audition_btn.setChecked(True)
    assert wait_until(app, lambda: win._on_clip, 20), win.cleanup.status.text()
    clip = win._audition["clip"]
    assert win.player.source().toLocalFile() == str(clip)
    assert win._src_offset == pytest.approx(99.0)
    assert win.wave.loop  # audition loops the range
    assert "PROCESSED" in win.cleanup.status.text()
    win.player.pause()
    win.seek(102.0)
    assert wait_until(app, lambda: abs(win._pos() - 102.0) < 0.15)
    win._tick()
    assert win.pos_label.text().endswith("00:04:37")  # full recording length, not the clip's
    assert win.view._current.sent.start_time <= 102.0  # transcript highlight in recording time

    # Changing a setting re-renders the audition (debounced).
    seq = win._audition_seq
    win.cleanup.presence.setValue(6)
    assert wait_until(app, lambda: win._audition_seq > seq and win._audition["clip"] != clip, 20)

    # A/B back to the original keeps the place.
    win.cleanup.audition_btn.setChecked(False)
    assert not win._on_clip and win._src_offset == 0
    assert win.player.source().toLocalFile() == str(win.playback_path)
    assert wait_until(app, lambda: abs(win._pos() - 102.0) < 0.25)

    # Jumping out of the auditioned range ends the audition.
    win.cleanup.audition_btn.setChecked(True)
    assert wait_until(app, lambda: win._on_clip, 20)
    win.seek(200.0)
    assert not win.cleanup.audition_btn.isChecked() and not win._on_clip
    assert wait_until(app, lambda: abs(win._pos() - 200.0) < 0.25)


def test_cleanup_plan_workflow(app, win, tmp_path, monkeypatch):
    from transcript_studio import audio as audio_mod
    from transcript_studio.ui.cleanup_panel import WHOLE
    from transcript_studio.model import Session
    c = win.cleanup
    tel = audio_mod.PRESETS["Telephone (200 Hz – 4 kHz)"]

    # Block 1: telephone on 100-104 s.
    win.wave.view.set_selection((100.0, 104.0))
    assert (c.start_spin.value(), c.end_spin.value()) == (100.0, 104.0)  # spins mirror the selection
    c.preset.setCurrentText("Telephone (200 Hz – 4 kHz)")
    c.save_btn.click()
    plan = win.session.cleanup_plan
    assert plan["blocks"] == [{"start": 100.0, "end": 104.0, "settings": tel.to_dict()}]
    assert win.editing == 0 and c.save_btn.text() == "Update block 1"
    assert win.wave.view.blocks == [(100.0, 104.0)] and win.dirty

    # A new, overlapping block is refused.
    c.new_btn.click()
    win.wave.view.set_selection((102.0, 106.0))
    c.save_btn.click()
    assert len(plan["blocks"]) == 1 and "overlaps block 1" in c.status.text()

    # A second block earlier in the file gets sorted to the front.
    win.wave.view.set_selection((50.0, 52.0))
    c.preset.setCurrentText("Gentle (good for transcription)")
    c.save_btn.click()
    assert [b["start"] for b in plan["blocks"]] == [50.0, 100.0] and win.editing == 0

    # Whole-file base settings.
    c.new_btn.click()
    c.mode_whole.setChecked(True)
    c.preset.setCurrentText("Normalize only")
    c.save_btn.click()
    assert plan["whole"] == audio_mod.CleanupSettings().to_dict() and win.editing == WHOLE

    # Pick block 2 (the telephone block) by clicking its band: settings and range come back.
    win.wave.view.blockClicked.emit(1)
    assert win.editing == 1 and c.mode_range.isChecked()
    assert c.settings() == tel and win.wave.selection == (100.0, 104.0)
    # Adjust its start numerically, then update.
    c.start_spin.setValue(99.5)
    assert win.wave.selection == (99.5, 104.0)
    c.save_btn.click()
    assert plan["blocks"][1]["start"] == 99.5

    # Delete block 1 (the gentle one).
    win._edit_plan_entry(0)
    c.delete_btn.click()
    assert [b["start"] for b in plan["blocks"]] == [99.5] and win.editing is None

    # The plan is part of the project file.
    win.project_path = tmp_path / "p.tsproj.json"
    assert win.save_project()
    assert Session.load(win.project_path).cleanup_plan == plan

    # Render: a new file with the plan applied, then the note tracks staleness.
    out = tmp_path / "rendered.flac"
    monkeypatch.setattr(audio_mod, "boosted_path", lambda p: out)
    win.render_plan()
    assert wait_until(app, lambda: win.worker is None, 120)
    assert out.exists() and win.boosted_path == out
    assert win.session.cleanup_rendered == plan
    assert "up to date" in c.plan_status.text()
    assert win.use_boost_check.isChecked() and c.saveas_btn.isEnabled()
    assert "block 1 99.50-104.00s" in win.boost_description
    win._edit_plan_entry(0)
    c.gain.setValue(3)
    c.save_btn.click()
    assert "changed since the last render" in c.plan_status.text()

    # Save the rendered audio as a new recording and open it (for another pass).
    new = tmp_path / "cleaned.flac"
    monkeypatch.setattr("transcript_studio.ui.main_window.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(new), ""))
    win.dirty = False
    win.save_rendered_as_recording()
    assert wait_until(app, lambda: win.worker is None and win.playback_path is not None, 30)
    assert win.audio_path == new and new.exists()
    assert win.session.cleanup_plan == {"whole": None, "blocks": []}


def test_sound_tag_inserted_saved_removed_and_reapplied(app, win, tmp_path, monkeypatch):
    from transcript_studio.model import Session
    monkeypatch.setattr("transcript_studio.ui.main_window.QInputDialog.getItem",
                        lambda *a, **k: ("sneeze", True))
    row = win.view._rows[20]  # a sentence with spread-out (proportional) times
    t = (row.sent.start_time + row.sent.end_time) / 2
    win.wave.view.set_selection((t, t + 0.3))
    assert win.wave.tag_btn.isEnabled()
    win.wave.tag_btn.click()
    assert win.session.sound_tags == [{"start": round(t, 3), "end": round(t + 0.3, 3), "label": "sneeze"}]
    texts = [s.text for _, s in win.session.iter_sentences()]
    assert sum("(sneeze)" in x for x in texts) == 1
    hit = next(r for r in win.view._rows if "(sneeze)" in r.edit.toPlainText())  # the view was refreshed
    assert win.dirty and win.wave.view.tags == [(round(t, 3), round(t + 0.3, 3), "sneeze")]

    # Saved with the project.
    win.project_path = tmp_path / "p.tsproj.json"
    win.save_project()
    assert Session.load(win.project_path).sound_tags == win.session.sound_tags

    # Re-transcribing inserts stored tags into the new transcript.
    fresh = parse_reference(REFERENCE.read_text(encoding="utf-8"))
    fresh.audio_path = win.session.audio_path
    for p, s in zip(fresh.paragraphs, win.session.paragraphs):
        for a, b in zip(p.sentences, s.sentences):
            a.start_time, a.end_time = b.start_time, b.end_time
    win._transcribed(fresh)
    assert sum("(sneeze)" in s.text for _, s in win.session.iter_sentences()) == 1

    # Removing the tag removes its text too.
    win.remove_sound_tag(0)
    assert win.session.sound_tags == []
    assert not any("(sneeze)" in s.text for _, s in win.session.iter_sentences())


def test_help_menu_manual_and_about(app, win):
    from transcript_studio import __version__
    from transcript_studio.ui.help import MANUAL, ManualWindow, about_html, manual_sections
    menus = [a.text().replace("&", "") for a in win.menuBar().actions()]
    assert menus[-1] == "Help"
    help_items = [a.text().replace("&", "") for a in win.menuBar().actions()[-1].menu().actions() if a.text()]
    assert help_items == ["User Manual", "About Transcript Studio"]

    sections = manual_sections(MANUAL.read_text(encoding="utf-8"))
    titles = [t for t, _ in sections]
    for needed in ("Overview", "Speech cleanup", "Transcribing", "Keyboard shortcuts"):
        assert needed in titles
    m = ManualWindow(win)
    assert m.toc.count() == len(sections) and "Overview" in m.browser.toPlainText()
    m.find_edit.setText("Ctrl+Shift+N")
    m.find_next()
    assert "Ctrl+Shift+N" in m.browser.toPlainText()
    win.show_manual()
    assert win._manual.isVisible()
    win._manual.close()

    html = about_html()
    assert f"Transcript Studio {__version__}" in html and "whisper.cpp" in html and "Logs:" in html


def test_manual_mentions_every_shortcut(win):
    from transcript_studio.ui.help import MANUAL
    manual = MANUAL.read_text(encoding="utf-8")
    shortcuts = {a.shortcut().toString() for m in win.menuBar().actions() for a in m.menu().actions()
                 if not a.shortcut().isEmpty()}
    missing = [s for s in shortcuts if s not in manual]
    assert not missing, f"shortcuts not documented in the manual: {missing}"


def test_export_dialog_and_main_window_export(app, win, tmp_path, monkeypatch):
    from transcript_studio.ui.export_dialog import ExportDialog
    d = ExportDialog(tmp_path, "talk", "md", True, win)
    assert d.path() == tmp_path / "talk.md"
    d.radios["pdf"].setChecked(True)
    assert d.path() == tmp_path / "talk.pdf" and d.fmt() == "pdf"
    d.details.setChecked(False)
    (tmp_path / "talk.pdf").write_text("old")
    asked = []
    monkeypatch.setattr("transcript_studio.ui.export_dialog.QMessageBox.question",
                        lambda *a, **k: asked.append(a[2]) or d.__class__.__mro__[0] and 0)
    d._accept()
    assert asked and "already exists" in asked[0] and d.result() == 0  # declined: dialog stays open

    # Main window: export as Word without details, to a folder of our choosing.
    out = tmp_path / "sub" / "my transcript.docx"
    out.parent.mkdir()

    class FakeDialog:
        Accepted = 1

        def __init__(self, folder, stem, fmt, details, parent):
            pass

        def exec(self):
            return 1

        def path(self):
            return out

        def fmt(self):
            return "docx"

        def include_details(self):
            return False

    monkeypatch.setattr("transcript_studio.ui.main_window.ExportDialog", FakeDialog)
    win.export_transcript()
    assert out.exists()
    assert win.settings.value("export_dir") == str(out.parent)
    assert win.settings.value("export_format") == "docx"
    assert win.settings.value("export_details", type=bool) is False
