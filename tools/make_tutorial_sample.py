"""Make the cleanup tutorial's deliberately imperfect sample from a clean recording.

    .venv/bin/python tools/make_tutorial_sample.py [--src CLEAN.mp3] [--out samples/tutorial]

Defects are injected in this order so each stays audible after the next:
a clipped passage, a low overall level, 60 Hz hum, background hiss.
Deterministic: the same source and SEED give the same samples.
Writes <name>-needs-cleanup.mp3 and <name>-needs-cleanup.answer-key.json.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tutorial_metrics import load_mono, measure  # noqa: E402

from transcript_studio import audio  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = 434
CLIP_START = 100.0  # seconds; the clipped passage
CLIP_LEN = 20.0
CLIP_BOOST_DB = 14.0
TARGET_LUFS = -35.0
HUM_HZ = 60.0
HUM_DBFS = -45.0  # RMS of the 60 Hz fundamental; 120 and 180 Hz are 8 dB lower each
HISS_DBFS = -52.0  # RMS of the white noise


def _db(db: float) -> float:
    return 10 ** (db / 20)


def degrade(pcm: np.ndarray, rate: int, seed: int = SEED) -> np.ndarray:
    """Return a float32 copy of mono `pcm` with the four defects applied."""
    x = pcm.astype(np.float64)
    start = int(CLIP_START * rate)
    end = min(len(x), int((CLIP_START + CLIP_LEN) * rate))
    x[start:end] = np.clip(x[start:end] * _db(CLIP_BOOST_DB), -1.0, 1.0)

    with tempfile.TemporaryDirectory(prefix="ts-degrade-") as tmp:
        wav = Path(tmp) / "x.wav"
        sf.write(wav, x.astype(np.float32), rate, subtype="FLOAT")
        loudness = audio.loudness_stats(wav)["integrated_lufs"]
    x *= _db(TARGET_LUFS - loudness)

    t = np.arange(len(x)) / rate
    for harmonic, drop_db in ((1, 0.0), (2, 8.0), (3, 16.0)):
        x += np.sqrt(2) * _db(HUM_DBFS - drop_db) * np.sin(2 * np.pi * HUM_HZ * harmonic * t)
    x += np.random.default_rng(seed).standard_normal(len(x)) * _db(HISS_DBFS)
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def recommended_plan() -> dict:
    """The cleanup the tutorial walks through, in the app's edit-plan format."""
    whole = audio.CleanupSettings(highpass_hz=70, hum_hz=60, denoise="fft", denoise_strength="medium",
                                  normalize=True)
    clip = audio.CleanupSettings(**{**whole.to_dict(), "declip": True})
    return {"whole": whole.to_dict(),
            "blocks": [{"start": CLIP_START, "end": CLIP_START + CLIP_LEN, "settings": clip.to_dict()}]}


def main(src: Path, dst_dir: Path) -> None:
    src, dst_dir = Path(src), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    pcm, rate = load_mono(src)
    stem = src.stem.replace("-2speakers", "") + "-needs-cleanup"
    mp3 = dst_dir / f"{stem}.mp3"
    with tempfile.TemporaryDirectory(prefix="ts-tutorial-") as tmp:
        wav = Path(tmp) / "degraded.wav"
        sf.write(wav, degrade(pcm, rate), rate, subtype="PCM_16")
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(wav), "-ac", "1",
                        "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
    key = {
        "source": src.name,
        "seed": SEED,
        "defects": {
            "clip": {"start": CLIP_START, "end": CLIP_START + CLIP_LEN, "boost_db": CLIP_BOOST_DB},
            "level": {"target_lufs": TARGET_LUFS},
            "hum": {"hz": HUM_HZ, "dbfs": HUM_DBFS},
            "hiss": {"dbfs": HISS_DBFS},
        },
        "recommended_plan": recommended_plan(),
        "measured_before": measure(mp3, (CLIP_START, CLIP_START + CLIP_LEN)),
    }
    (dst_dir / f"{stem}.answer-key.json").write_text(json.dumps(key, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {mp3} and its answer key")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(ROOT / "samples/multispeaker/nasa-podcast-ep434-2speakers.mp3"))
    ap.add_argument("--out", default=str(ROOT / "samples/tutorial"))
    args = ap.parse_args()
    main(Path(args.src), Path(args.out))
