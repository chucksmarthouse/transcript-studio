"""The measurements the cleanup tutorial quotes: hum, hiss (via dynamic range), clipping, loudness."""

import subprocess
import sys

import numpy as np
import soundfile as sf

from tests.test_export import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from tutorial_metrics import hum_db, measure, near_peak_fraction, snr_db  # noqa: E402

RATE = 16000


def _t(seconds=10):
    return np.arange(RATE * seconds) / RATE


def _noise(seconds=10, rms=0.01, seed=0):
    return (np.random.default_rng(seed).standard_normal(RATE * seconds) * rms).astype(np.float32)


def _bursts(seconds=10):
    """Tone bursts in silence: a stand-in for speech with pauses."""
    t = _t(seconds)
    gate = (np.floor(t * 2) % 2 == 0).astype(np.float32)
    return (0.3 * np.sin(2 * np.pi * 440 * t) * gate).astype(np.float32)


def test_hum_db_detects_60hz():
    t = _t()
    humming = _noise() + (0.05 * np.sin(2 * np.pi * 60 * t)).astype(np.float32)
    assert hum_db(humming, RATE) > 15
    assert hum_db(_noise(), RATE) < 6


def test_snr_db_drops_with_added_hiss():
    clean = _bursts() + _noise(rms=1e-4)
    hissy = _bursts() + _noise(rms=0.02)
    assert snr_db(clean, RATE) - snr_db(hissy, RATE) >= 10


def test_near_peak_fraction_flags_clipping_after_lossy_round_trip(tmp_path):
    # Gaussian noise stands in for speech: unlike a sine (29% of samples near its peak), its
    # peaks are rare, so only real flat-topping gives a high fraction.
    noise = _noise(5, rms=0.1)
    clipped = np.clip(noise, -0.1, 0.1)
    fractions = {}
    for name, x in (("noise", noise), ("clipped", clipped)):
        wav, mp3, back = tmp_path / f"{name}.wav", tmp_path / f"{name}.mp3", tmp_path / f"{name}.back.wav"
        sf.write(wav, x, RATE)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(wav), "-b:a", "128k", str(mp3)], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(mp3), str(back)], check=True)
        fractions[name] = near_peak_fraction(sf.read(back)[0].astype(np.float32))
    # Measured after the MP3 round trip: ~0.035 clipped at 1 sigma (32% of samples flat-topped
    # before encoding; the encoder's ringing smears them), ~0.008 unclipped.
    assert fractions["noise"] < 0.012
    assert fractions["clipped"] > 3 * fractions["noise"]


def test_measure_returns_all_keys(tmp_path):
    wav = tmp_path / "x.wav"
    sf.write(wav, _bursts(5) + _noise(5), RATE)
    assert set(measure(wav)) == {"hum_db", "snr_db", "near_peak_fraction", "integrated_lufs"}
