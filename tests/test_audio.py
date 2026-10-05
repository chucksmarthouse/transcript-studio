"""The FLAC playback copy and every boost chain must keep one sample-accurate timeline."""

import os
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import correlate

from tests.test_export import ROOT
from transcript_studio import audio

SAMPLE = ROOT / "samples" / "multispeaker" / "nasa-podcast-ep434-2speakers.mp3"


@pytest.fixture(scope="module")
def excerpt(tmp_path_factory):
    """60 s of the sample re-encoded as stereo AAC, starting at 2:00 (has speech throughout)."""
    out = tmp_path_factory.mktemp("audio") / "excerpt.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "120", "-t", "60",
                    "-i", str(SAMPLE), "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)], check=True)
    return out


def lag_samples(a: np.ndarray, b: np.ndarray, max_lag: int) -> int:
    """Delay of b relative to a, by cross-correlating amplitude envelopes."""
    n = len(a) - 2 * max_lag
    x = np.abs(a[max_lag:max_lag + n])
    y = np.abs(b[:n + 2 * max_lag])
    c = correlate(y - y.mean(), x - x.mean(), "valid", method="fft")
    return int(np.argmax(c)) - max_lag


def test_playback_copy_is_mono_flac_same_rate_and_length(excerpt):
    pb = audio.make_playback_copy(excerpt)
    assert pb.name == "excerpt.ts.flac"
    info = sf.info(pb)
    assert (info.format, info.channels, info.subtype) == ("FLAC", 1, "PCM_16")
    assert info.samplerate == audio.sample_rate(excerpt) == 48000
    assert abs(info.duration - audio.duration(excerpt)) < 0.03  # within one AAC frame


def test_playback_copy_reused_even_if_source_is_future_dated(excerpt):
    future = time.time_ns() + 6 * 3600 * 10**9  # e.g. copied in from a skewed clock
    os.utime(excerpt, ns=(future, future))
    pb = audio.make_playback_copy(excerpt)
    inode = pb.stat().st_ino
    assert audio.make_playback_copy(excerpt) == pb
    assert pb.stat().st_ino == inode, "playback copy was rewritten instead of reused"


def test_playback_copy_redone_when_source_replaced(excerpt):
    pb = audio.make_playback_copy(excerpt)
    inode = pb.stat().st_ino
    os.utime(excerpt)  # source "replaced": mtime no longer matches the stamp
    audio.make_playback_copy(excerpt)
    assert pb.stat().st_ino != inode


def test_cancel_leaves_no_partial_file(excerpt, tmp_path):
    src = tmp_path / "c.m4a"
    shutil.copy(excerpt, src)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(audio.Cancelled):
        audio.make_playback_copy(src, cancel=cancel)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.m4a"]


C = audio.CleanupSettings
CLEANUP_CASES = {
    "normalize": C(),
    "gain": C(normalize=False, gain_db=6),
    "highpass": C(normalize=False, highpass_hz=150),
    "lowpass": C(normalize=False, lowpass_hz=4000),
    "hum": C(normalize=False, hum_hz=60),
    "presence": C(normalize=False, presence_db=6),
    "declick": C(normalize=False, declick=True),
    "declip": C(normalize=False, declip=True),
    "fft": C(normalize=False, denoise="fft", denoise_strength="strong"),
    "wavelet": C(normalize=False, denoise="wavelet", denoise_strength="strong"),
    "rnnoise": C(normalize=False, denoise="rnnoise", denoise_strength="strong"),
    "nlm": C(normalize=False, denoise="nlm"),
    "speechnorm": C(normalize=False, leveler="speechnorm"),
    "dynaudnorm": C(normalize=False, leveler="dynaudnorm"),
    "left": C(normalize=False, channel="left"),
    "right": C(normalize=False, channel="right"),
    "everything": C(declick=True, declip=True, highpass_hz=100, lowpass_hz=6000, hum_hz=60, presence_db=4,
                    denoise="rnnoise", leveler="speechnorm", gain_db=2),
    **{f"preset {name}": preset for name, preset in audio.PRESETS.items()},
}


@pytest.mark.parametrize("settings", CLEANUP_CASES.values(), ids=CLEANUP_CASES.keys())
def test_cleanup_keeps_timeline(excerpt, settings, tmp_path):
    """Processing the original must line up sample-for-sample with the playback copy."""
    pb = audio.make_playback_copy(excerpt)
    out = audio.process(excerpt, tmp_path / "p.flac", settings)
    a, ra = sf.read(pb)
    b, rb = sf.read(out)
    assert b.ndim == 1 and rb == ra, "output must be mono at the source rate"
    assert abs(len(b) - len(a)) <= 1, f"length changed by {len(b) - len(a)} samples"
    n = min(len(a), len(b))
    lag = lag_samples(a[:n], b[:n], max_lag=ra // 10)
    # IIR EQ filters (a steep 200 Hz low cut most of all) add up to ~1 ms of
    # frequency-dependent phase shift, which is irrelevant for word-level sync.
    # Uncompensated denoiser delays are 8-25 ms, so 2 ms still catches those.
    assert abs(lag) <= ra * 0.002, f"cleanup shifted audio by {lag} samples ({lag / ra * 1000:.1f} ms)"


def test_left_minus_right_runs(excerpt, tmp_path):
    out = audio.process(excerpt, tmp_path / "d.flac", C(channel="diff"))
    assert sf.info(out).channels == 1


def test_audition_clip_lines_up_with_full_file(excerpt, tmp_path):
    settings = C(highpass_hz=100, denoise="fft")
    full = audio.process(excerpt, tmp_path / "full.flac", settings)
    clip = audio.render_clip(excerpt, tmp_path / "clip.flac", settings, start=20.0, length=6.0)
    a, ra = sf.read(full)
    b, _ = sf.read(clip)
    assert abs(len(b) - 6.0 * ra) <= 2
    seg = a[int(20.0 * ra):int(26.0 * ra)]
    n = min(len(seg), len(b))
    assert abs(lag_samples(seg[:n], b[:n], max_lag=ra // 10)) <= 5


def test_settings_round_trip_and_describe():
    s = C(channel="left", highpass_hz=90, denoise="rnnoise", leveler="speechnorm")
    assert C.from_dict(s.to_dict()) == s
    assert C.from_dict({"highpass_hz": 80, "unknown_old_key": 1}) == C(highpass_hz=80)
    assert "low cut 90 Hz" in s.describe() and "RNNoise" in s.describe()
    assert C(normalize=False).is_identity() and not C().is_identity()


def test_opening_a_derived_copy(tmp_path, excerpt):
    src = tmp_path / "talk.m4a"
    shutil.copy(excerpt, src)
    pb = audio.make_playback_copy(src)
    assert audio.is_derived(pb) and not audio.is_derived(src)
    assert audio.original_of(pb) == src
    assert audio.original_of(tmp_path / "talk.boosted.flac") == src
    assert audio.make_playback_copy(pb) == pb  # no copy-of-a-copy
    assert not (tmp_path / "talk.ts.ts.flac").exists()


# ---------------------------------------------------------------- edit plan render

def _plan(*blocks, whole=None):
    return {"whole": whole.to_dict() if whole else None,
            "blocks": [{"start": s, "end": e, "settings": st.to_dict()} for s, e, st in blocks]}


def _low_band_share(x, rate):
    """Share of the signal's RMS below 60 Hz (independent of overall loudness)."""
    from scipy.signal import butter, sosfilt
    low = sosfilt(butter(4, 60, "low", fs=rate, output="sos"), x)
    return float(np.sqrt(np.mean(low ** 2)) / np.sqrt(np.mean(x ** 2)))


def test_render_plan_splices_blocks_and_leaves_the_rest_untouched(excerpt, tmp_path):
    tel = audio.PRESETS["Telephone (200 Hz – 4 kHz)"]
    plan = _plan((10.0, 15.0, tel), (30.0, 32.0, C(normalize=False, denoise="fft", gain_db=3)))
    out = audio.render_plan(excerpt, tmp_path / "plan.flac", plan)
    pb = audio.make_playback_copy(excerpt)
    a, ra = sf.read(pb)
    b, rb = sf.read(out)
    assert rb == ra and len(b) == len(a)
    assert audio.is_derived_from(out, excerpt)
    seg = lambda x, t0, t1: x[int(t0 * ra):int(t1 * ra)]
    # Outside the blocks: identical to the original playback copy.
    for t0, t1 in [(0, 9.99), (15.01, 29.99), (32.01, 59.9)]:
        assert np.max(np.abs(seg(a, t0, t1) - seg(b, t0, t1))) < 1e-4, (t0, t1)
    # Inside block 1: band-limited (200 Hz low cut) and still time-aligned.
    assert _low_band_share(seg(b, 10.1, 14.9), ra) < 0.5 * _low_band_share(seg(a, 10.1, 14.9), ra)  # measured 0.35
    assert abs(lag_samples(seg(a, 10.1, 14.9), seg(b, 10.1, 14.9), ra // 20)) <= ra * 0.002
    # Crossfaded edges: no step bigger than the audio's own sample-to-sample movement.
    jump = np.max(np.abs(np.diff(b)))
    for t in (10.0, 15.0, 30.0, 32.0):
        i = int(t * ra)
        assert np.max(np.abs(np.diff(b[i - 50:i + 50]))) <= jump


def test_render_plan_whole_file_base_with_block(excerpt, tmp_path):
    whole = C(highpass_hz=100)  # + normalize
    plan = _plan((20.0, 25.0, C(normalize=False, lowpass_hz=3000)), whole=whole)
    out = audio.render_plan(excerpt, tmp_path / "plan.flac", plan)
    full = audio.process(excerpt, tmp_path / "whole.flac", whole)
    a, ra = sf.read(full)
    b, _ = sf.read(out)
    assert len(a) == len(b)
    assert np.max(np.abs(a[:int(19.9 * ra)] - b[:int(19.9 * ra)])) < 1e-4
    assert np.max(np.abs(a[int(25.1 * ra):] - b[int(25.1 * ra):])) < 1e-4
    assert "whole file: low cut 100 Hz" in audio.describe_plan(plan) and "block 1 20.00-25.00s" in audio.describe_plan(plan)


def test_cleanup_plan_saved_in_project(tmp_path):
    from transcript_studio.model import Session
    s = Session(audio_path="x.m4a", model_size="")
    s.cleanup_plan = _plan((1.5, 3.25, C(hum_hz=60)), whole=C())
    s.cleanup_rendered = s.cleanup_plan
    s.save(tmp_path / "p.json")
    back = Session.load(tmp_path / "p.json")
    assert back.cleanup_plan == s.cleanup_plan and back.cleanup_rendered == s.cleanup_plan
    assert audio.plan_blocks(back.cleanup_plan)[0] == (1.5, 3.25, C(hum_hz=60))
    old = Session.from_dict({"audio_path": "x", "model_size": "m"})  # projects from before plans existed
    assert old.cleanup_plan == {"whole": None, "blocks": []}
