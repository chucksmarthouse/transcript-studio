"""The tutorial's deliberately imperfect sample: generation is deterministic and injects every defect."""

import sys

import numpy as np
import pytest
import soundfile as sf

from tests.test_export import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import make_tutorial_sample as mts  # noqa: E402
from tutorial_metrics import hum_db, near_peak_fraction, snr_db  # noqa: E402

from transcript_studio import audio  # noqa: E402

RATE = 16000


def speech_like(seconds=45, seed=0):
    """Gaussian noise gated by a 4 Hz square envelope (talk and pause), over a faint floor."""
    rng = np.random.default_rng(seed)
    t = np.arange(RATE * seconds) / RATE
    gate = (np.floor(t * 4) % 2 == 0).astype(np.float32)
    voice = rng.standard_normal(len(t)).astype(np.float32) * 0.1 * gate
    return voice + rng.standard_normal(len(t)).astype(np.float32) * 1e-4


def test_degrade_is_deterministic():
    x = speech_like(20)
    assert np.array_equal(mts.degrade(x, RATE), mts.degrade(x, RATE))
    assert not np.array_equal(mts.degrade(x, RATE), mts.degrade(x, RATE, seed=1))


def test_degrade_applies_each_defect(tmp_path, monkeypatch):
    monkeypatch.setattr(mts, "CLIP_START", 10.0)
    clean = speech_like()
    out = mts.degrade(clean, RATE)
    assert out.dtype == np.float32 and np.abs(out).max() <= 1.0
    # clipped passage: flat-topped inside 10-30 s, not after it
    # (measured 0.022 vs 0.009 on this synthetic signal, whose 2-sigma clipping is mild; the real
    # sample is checked against the metric's own numbers in test_shipped_sample_has_every_defect)
    assert near_peak_fraction(out, (10, 30, RATE)) > 2 * near_peak_fraction(out, (35, 45, RATE))
    # low level
    wav = tmp_path / "out.wav"
    sf.write(wav, out, RATE)
    assert audio.loudness_stats(wav)["integrated_lufs"] == pytest.approx(mts.TARGET_LUFS, abs=1.5)
    # hum and hiss
    assert hum_db(out, RATE) > 10 > hum_db(clean, RATE)
    assert snr_db(out, RATE) < snr_db(clean, RATE) - 8


# ---- the shipped sample and the recommended cleanup --------------------------------------

import json  # noqa: E402

from tutorial_metrics import measure  # noqa: E402

TUTORIAL = ROOT / "samples" / "tutorial"
MP3 = TUTORIAL / "nasa-podcast-ep434-needs-cleanup.mp3"
KEY = json.loads((TUTORIAL / "nasa-podcast-ep434-needs-cleanup.answer-key.json").read_text(encoding="utf-8"))
CLIP = (KEY["defects"]["clip"]["start"], KEY["defects"]["clip"]["end"])


def test_shipped_sample_has_every_defect():
    before = KEY["measured_before"]
    assert before["hum_db"] > 10
    assert before["snr_db"] < 20
    assert -36.5 <= before["integrated_lufs"] <= -33.5
    assert before["near_peak_fraction"] > 0.05
    assert 1 < MP3.stat().st_size / 1e6 < 6
    now = measure(MP3, CLIP)  # the key describes the file that ships (tolerances: ffmpeg/scipy versions)
    assert now["hum_db"] == pytest.approx(before["hum_db"], abs=0.1)
    assert now["snr_db"] == pytest.approx(before["snr_db"], abs=0.1)
    assert now["near_peak_fraction"] == pytest.approx(before["near_peak_fraction"], abs=0.002)
    assert now["integrated_lufs"] == pytest.approx(before["integrated_lufs"], abs=0.2)


def test_recommended_plan_fixes_every_defect(tmp_path):
    before = KEY["measured_before"]
    clean = tmp_path / "clean.flac"
    audio.render_plan(MP3, clean, KEY["recommended_plan"])
    after = measure(clean, CLIP)
    print("before:", before, "\nafter: ", after)
    assert after["hum_db"] < before["hum_db"] - 10
    assert after["snr_db"] > before["snr_db"] + 6
    # Measured: hum 34.3 -> -5.7 dB, snr 11.4 -> 24.1 dB, near-peak 0.172 -> 0.009 (clean source: 0.008),
    # loudness -35.2 -> -17.1 LUFS (the app's single-pass loudnorm lands ~1 LU under its -16 target).
    assert -18.0 <= after["integrated_lufs"] <= -15.0
    assert after["near_peak_fraction"] <= 0.5 * before["near_peak_fraction"]
