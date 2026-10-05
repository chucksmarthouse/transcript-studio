"""Measure the four defects the cleanup tutorial teaches you to fix.

    .venv/bin/python tools/tutorial_metrics.py FILE [--clip START END]

Prints hum_db, snr_db, near_peak_fraction (over the --clip span, else the whole file)
and integrated_lufs. All but integrated_lufs are scale-invariant, so they compare a file
before and after loudness normalisation.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import welch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcript_studio import audio  # noqa: E402


def load_mono(path) -> tuple[np.ndarray, int]:
    """Decode any audio file at its own sample rate, mixed to mono float32 in [-1, 1]."""
    with tempfile.TemporaryDirectory(prefix="ts-metrics-") as tmp:
        wav = Path(tmp) / "x.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1",
                        "-c:a", "pcm_f32le", str(wav)], check=True)
        x, rate = sf.read(wav, dtype="float32")
    return x, rate


def hum_db(x: np.ndarray, rate: int, hum_hz: float = 60.0) -> float:
    """Peak PSD within ±1 Hz of hum_hz over the mean PSD of the neighbouring bands, in dB."""
    f, p = welch(x, fs=rate, nperseg=rate)
    peak = p[(f >= hum_hz - 1) & (f <= hum_hz + 1)].max()
    around = p[((f >= hum_hz - 15) & (f <= hum_hz - 5)) | ((f >= hum_hz + 5) & (f <= hum_hz + 15))].mean()
    return float(10 * np.log10(peak / around))


def snr_db(x: np.ndarray, rate: int) -> float:
    """90th minus 10th percentile of 50 ms-window RMS, in dB: how far speech stands above the floor."""
    n = int(rate * 0.05)
    frames = x[: len(x) // n * n].reshape(-1, n)
    rms_db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    return float(np.percentile(rms_db, 90) - np.percentile(rms_db, 10))


def near_peak_fraction(x: np.ndarray, window: tuple[float, float, int] | None = None) -> float:
    """Fraction of samples above 80% of the 99.9th-percentile peak. High when peaks are flat-topped.

    window is (start_s, end_s, rate). Lossy encoding rings past a clipped plateau, so the reference
    is a high percentile rather than the single largest sample, and exact plateau runs are not used."""
    if window is not None:
        start, end, rate = window
        x = x[int(start * rate): int(end * rate)]
    a = np.abs(x)
    return float((a >= 0.8 * np.percentile(a, 99.9)).mean())


def measure(path, clip_range: tuple[float, float] | None = None) -> dict:
    x, rate = load_mono(path)
    window = (*clip_range, rate) if clip_range else None
    return {
        "hum_db": round(hum_db(x, rate), 2),
        "snr_db": round(snr_db(x, rate), 2),
        "near_peak_fraction": round(near_peak_fraction(x, window), 5),
        "integrated_lufs": audio.loudness_stats(path)["integrated_lufs"],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--clip", nargs=2, type=float, metavar=("START", "END"))
    args = ap.parse_args()
    for key, value in measure(args.file, tuple(args.clip) if args.clip else None).items():
        print(f"{key}: {value}")
