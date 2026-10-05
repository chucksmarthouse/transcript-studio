"""Waveform: selection playback logic, zoom/follow, drag-to-select, transcript coloring."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from transcript_studio import audio
from transcript_studio.envelope import ENV_RATE, compute_envelope
from transcript_studio.export import speaker_style
from transcript_studio.model import Paragraph, Sentence, Session
from transcript_studio.ui.waveform import WaveformView, selection_action
from tests.test_export import ROOT


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app):
    v = WaveformView()
    v.resize(1000, 120)
    v.show()
    v.set_envelope(np.abs(np.sin(np.arange(60 * ENV_RATE) / 50)).astype(np.float32))  # 60 s
    return v


def test_selection_action():
    sel = (10.0, 12.0)
    assert selection_action(11.9, 11.8, sel, loop=False) is None
    assert selection_action(12.02, 11.98, sel, loop=False) == "stop"
    assert selection_action(12.02, 11.98, sel, loop=True) == "loop"
    assert selection_action(30.0, 5.0, sel, loop=False) is None  # user jumped past: not a crossing
    assert selection_action(12.02, 11.98, None, loop=False) is None


def test_envelope_of_real_file():
    path = ROOT / "samples" / "multispeaker" / "nasa-podcast-ep434-2speakers.mp3"
    env = compute_envelope(path)
    assert abs(len(env) / ENV_RATE - audio.duration(path)) < 0.05
    assert 0 < env.max() <= 1


def test_zoom_and_fit(view):
    assert view.view_span == pytest.approx(60)
    view.zoom(4, anchor=30)
    assert view.view_span == pytest.approx(15)
    assert view.view_start <= 30 <= view.view_start + view.view_span
    view.zoom(1000)
    assert view.view_span == pytest.approx(1.0)  # clamped at MIN_SPAN
    view.fit()
    assert (view.view_start, view.view_span) == (0, pytest.approx(60))


def test_follow_keeps_playhead_visible(view):
    view.zoom(6)  # 10 s visible
    view.set_view(0)
    view.set_playhead(25.0, follow=True)
    assert view.view_start <= 25.0 <= view.view_start + 0.92 * view.view_span
    start = view.view_start
    view.set_playhead(40.0, follow=False)  # not following: view stays put
    assert view.view_start == start


def test_drag_selects_and_click_clears(view):
    spy = QSignalSpy(view.selectionChanged)
    QTest.mousePress(view, Qt.LeftButton, pos=QPoint(100, 60))
    QTest.mouseMove(view, QPoint(300, 60))
    QTest.mouseRelease(view, Qt.LeftButton, pos=QPoint(300, 60))
    assert view.selection == (pytest.approx(6.0, abs=0.1), pytest.approx(18.0, abs=0.1))
    QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(500, 60))
    assert view.selection is None
    assert spy.count() == 2


def test_colors_follow_transcript(view):
    s = Session(audio_path="x", model_size="m")
    s.paragraphs = [
        Paragraph(1, None, True, 1.0, [Sentence(1, "(cough)", start_time=1.0, end_time=2.0)]),
        Paragraph(2, 2, False, 3.0, [Sentence(1, "Hello.", start_time=3.0, end_time=5.0),
                                     Sentence(2, "Mumble.", True, start_time=5.0, end_time=6.0)]),
    ]
    view.set_session(s)
    color_at = lambda t: view.palette[view.cidx[int(t * ENV_RATE)]].name()
    assert color_at(0.5) == "#94a3b8"  # untranscribed: neutral
    assert color_at(1.5) == "#6b7280"  # non-speech gray
    assert color_at(4.0) == speaker_style(2)[1]  # Participant 2 green
    assert color_at(5.5) == "#dc2626"  # low-confidence red
    s.paragraphs[1].speaker_id = 3  # reassigning recolors
    view.set_session(s)
    assert color_at(4.0) == speaker_style(3)[1]


def test_drag_selection_edges(view):
    view.set_selection((10.0, 20.0))  # x = 166.7 .. 333.3 at 60 s / 1000 px
    y = 80
    QTest.mousePress(view, Qt.LeftButton, pos=QPoint(334, y))  # grab the end edge
    QTest.mouseMove(view, QPoint(500, y))
    QTest.mouseRelease(view, Qt.LeftButton, pos=QPoint(500, y))
    assert view.selection == (pytest.approx(10.0), pytest.approx(30.0, abs=0.1))
    QTest.mousePress(view, Qt.LeftButton, pos=QPoint(167, y))  # grab the start edge
    QTest.mouseMove(view, QPoint(100, y))
    QTest.mouseRelease(view, Qt.LeftButton, pos=QPoint(100, y))
    assert view.selection == (pytest.approx(6.0, abs=0.1), pytest.approx(30.0, abs=0.1))


def test_block_strip_click(view):
    from transcript_studio.ui.waveform import RULER_H
    view.set_blocks([(5.0, 8.0), (40.0, 45.0)], active=None)
    spy = QSignalSpy(view.blockClicked)
    QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(int(42 / 60 * 1000), RULER_H + 5))
    assert spy.count() == 1 and spy.at(0)[0] == 1
    assert view.selection is None  # clicking the strip doesn't start a selection


def test_words_lane_only_when_zoomed_in(view):
    from transcript_studio.model import Word
    from transcript_studio.ui.waveform import WORDS_MAX_SPAN
    ws = [Word("Hello", 10.0, 10.4, 0.9), Word("(sneeze)", 10.5, 10.9, 1.0), Word("there.", 11.0, 11.5, 0.9)]
    s = Session(audio_path="x", model_size="m")
    s.paragraphs = [Paragraph(1, 1, False, 10.0, [Sentence(1, "Hello (sneeze) there.", words=ws,
                                                           start_time=10.0, end_time=11.5)])]
    s.sound_tags = [{"start": 20.0, "end": 21.0, "label": "horn"}]
    view.set_session(s)
    view.set_show_words(True)
    view.fit()  # 60 s visible: too far out to read words
    assert view.view_span > WORDS_MAX_SPAN and view.visible_words() == []
    assert view.tags == [(20.0, 21.0, "horn")]  # tags are known regardless of zoom
    view.set_view(5.0, 20.0)
    words = view.visible_words()
    assert [(w[0], w[2], w[4]) for w in words] == [(10.0, "Hello", False), (10.5, "(sneeze)", True),
                                                   (11.0, "there.", False)]
    assert words[0][3] == speaker_style(1)[1]
    view.set_show_words(False)
    assert view.visible_words() == []
