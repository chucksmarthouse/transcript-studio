"""ffmpeg-based audio handling (spec §5 steps 1-2).

Every input is first converted to a *playback copy*: mono 16-bit FLAC at the
source's sample rate. Playback, boosting and transcription all work from that
copy, so they share one sample-accurate timeline whatever the original
container was (VBR MP3s in particular seek inaccurately in media players).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .logs import log

SAMPLE_RATE = 16000  # model input rate
PLAYBACK_SUFFIX = ".ts.flac"
BOOSTED_SUFFIX = ".boosted.flac"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "transcript-studio"

# afftdn delays its output by a fixed 25 ms at any sample rate (measured with
# ffmpeg 8.0; tests/test_audio.py re-checks it). boost() trims it back off.
AFFTDN_DELAY_SECONDS = 0.025

ProgressFn = Callable[[float, str], None]


class Cancelled(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.debug("Running: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()[-2000:]
        log.error("%s failed (exit %d): %s", cmd[0], proc.returncode, err)
        raise RuntimeError(f"{cmd[0]} failed: {err}")
    return proc


def _run_ffmpeg(args: list[str], total_seconds: float, message: str,
                progress: ProgressFn | None, cancel: threading.Event | None) -> None:
    """Run ffmpeg with progress reporting (via -progress) and cancellation."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats",
           "-progress", "pipe:1", "-y", *args]
    log.info("Running: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled()
        if progress and line.startswith("out_time_us=") and total_seconds > 0:
            try:
                progress(min(int(line.split("=", 1)[1]) / 1e6 / total_seconds, 1.0), message)
            except ValueError:  # "N/A" before the first frame
                pass
    err = proc.stderr.read()
    if (rc := proc.wait()) != 0:
        log.error("ffmpeg failed (exit %d): %s", rc, err.strip()[-2000:])
        raise RuntimeError(f"ffmpeg failed: {err.strip()[-2000:]}")


def duration(path: str | Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.stdout.strip())


def sample_rate(path: str | Path) -> int:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                "-of", "csv=p=0", str(path)])
    return int(out.stdout.strip())


# ---------------------------------------------------------------- derived files
#
# A derived file (playback copy, boosted copy) is stamped with its source's exact
# mtime, and reused only while the two still match. Unlike "newer than source",
# this survives clock skew (files copied in with future timestamps) and still
# notices a replaced source.

def is_derived_from(derived: Path, source: Path) -> bool:
    return derived.exists() and derived.stat().st_mtime_ns == source.stat().st_mtime_ns


def _write_derived(source: Path, dst: Path, ffmpeg_args_for, message: str,
                   progress: ProgressFn | None, cancel: threading.Event | None) -> Path:
    """Run ffmpeg into `<dst>.part`, then atomically move it into place and stamp it."""
    tmp = dst.with_name(dst.name + ".part")
    try:
        _run_ffmpeg(ffmpeg_args_for(tmp), duration(source), message, progress, cancel)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    st = source.stat()
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    tmp.replace(dst)
    return dst


def _sibling_or_cache(src: Path, suffix: str) -> Path:
    """`<dir>/<stem><suffix>` next to the source, or in the cache dir if that's read-only."""
    if os.access(src.parent, os.W_OK):
        return src.with_name(src.stem + suffix)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:12]
    return CACHE_DIR / f"{src.stem}-{digest}{suffix}"


def playback_path(src: str | Path) -> Path:
    return _sibling_or_cache(Path(src), PLAYBACK_SUFFIX)


def boosted_path(src: str | Path) -> Path:
    return _sibling_or_cache(Path(src), BOOSTED_SUFFIX)


AUDIO_EXTENSIONS = (".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".webm")


def is_derived(path: str | Path) -> bool:
    """True for the app's own copies (<name>.ts.flac / <name>.boosted.flac)."""
    return Path(path).name.endswith((PLAYBACK_SUFFIX, BOOSTED_SUFFIX))


def original_of(path: str | Path) -> Path | None:
    """For a derived copy, the source file next to it (same stem, audio extension), if any."""
    path = Path(path)
    for suffix in (PLAYBACK_SUFFIX, BOOSTED_SUFFIX):
        if path.name.endswith(suffix):
            stem = path.name[:-len(suffix)]
            for ext in AUDIO_EXTENSIONS:
                cand = path.with_name(stem + ext)
                if cand.exists() and not is_derived(cand):
                    return cand
    return None


def make_playback_copy(src: str | Path, progress: ProgressFn | None = None,
                       cancel: threading.Event | None = None) -> Path:
    """Convert `src` to the standard playback format; reuses an up-to-date existing copy."""
    src = Path(src)
    if src.name.endswith(PLAYBACK_SUFFIX):
        return src  # already a playback copy: don't make a copy of the copy
    dst = playback_path(src)
    if is_derived_from(dst, src):
        return dst
    # First audio stream only (drops video / cover art), mono, 16-bit, rate unchanged.
    return _write_derived(
        src, dst,
        lambda tmp: ["-i", str(src), "-map", "0:a:0", "-ac", "1", "-sample_fmt", "s16",
                     "-c:a", "flac", "-f", "flac", str(tmp)],
        "Converting to FLAC…", progress, cancel)


# ---------------------------------------------------------------- model input

def decode_to_wav(src: str | Path, dst: str | Path) -> Path:
    """Decode to 16 kHz mono 16-bit PCM WAV (model input)."""
    _run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
          "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    return Path(dst)


def load_pcm(path: str | Path) -> np.ndarray:
    """Decode any audio file to a float32 16 kHz mono array in [-1, 1]."""
    proc = _run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
                 "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "-"])
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- speech cleanup

ROOT = Path(__file__).resolve().parent.parent
RNNOISE_MODEL = ROOT / "models" / "rnnoise" / "cb.rnnn"
RNNOISE_URL = "https://raw.githubusercontent.com/richardpl/arnndn-models/master/cb.rnnn"  # public, no account

# Fixed output delays (seconds) of the denoisers, measured with ffmpeg 8.0 at
# 44.1 and 48 kHz; trimmed back off so the result stays sample-aligned.
# Every other filter in the chain has no measurable delay (tests/test_audio.py
# re-checks all of them).
DENOISE_DELAY = {"fft": 0.025, "nlm": 0.008, "rnnoise": 0.010, "wavelet": 0.0}
_STRENGTH = {
    "fft": {"light": "afftdn=nr=6:nf=-40:tn=1", "medium": "afftdn=nr=12:nf=-40:tn=1",
            "strong": "afftdn=nr=20:nf=-40:tn=1"},
    "nlm": {"light": "anlmdn=s=0.0001", "medium": "anlmdn=s=0.0005", "strong": "anlmdn=s=0.002"},
    "wavelet": {"light": "afwtdn=adaptive=1:percent=50", "medium": "afwtdn=adaptive=1:percent=85",
                "strong": "afwtdn=adaptive=1:percent=100"},
    "rnnoise": {"light": "mix=0.5", "medium": "mix=0.8", "strong": "mix=1"},
}
DENOISERS = {"off": "Off", "fft": "FFT (afftdn)", "wavelet": "Wavelet (afwtdn)",
             "rnnoise": "RNNoise (neural)", "nlm": "NL-means (slow)"}
LEVELERS = {"off": "Off", "speechnorm": "Speech leveler (speechnorm)", "dynaudnorm": "Dynamic (dynaudnorm)"}
CHANNELS = {"mix": "Mix to mono", "left": "Left only", "right": "Right only", "diff": "Left − right"}


@dataclass
class CleanupSettings:
    """Speech-cleanup filter chain (applied to the original file, output mono FLAC)."""
    channel: str = "mix"  # mix | left | right | diff
    highpass_hz: int = 0  # low cut; 0 = off
    lowpass_hz: int = 0  # high cut; 0 = off
    hum_hz: int = 0  # 50 / 60: notch the hum and its harmonics; 0 = off
    presence_db: float = 0.0  # boost around 3 kHz for clarity
    declick: bool = False
    declip: bool = False
    denoise: str = "off"  # off | fft | wavelet | rnnoise | nlm
    denoise_strength: str = "medium"  # light | medium | strong
    leveler: str = "off"  # off | speechnorm | dynaudnorm
    normalize: bool = True  # EBU R128 loudnorm to -16 LUFS
    gain_db: float = 0.0

    def is_identity(self) -> bool:
        return self == CleanupSettings(normalize=False)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "CleanupSettings":
        names = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def describe(self) -> str:
        parts = []
        if self.channel != "mix":
            parts.append(CHANNELS[self.channel].lower())
        if self.declip:
            parts.append("de-clip")
        if self.declick:
            parts.append("de-click")
        if self.highpass_hz:
            parts.append(f"low cut {self.highpass_hz} Hz")
        if self.lowpass_hz:
            parts.append(f"high cut {self.lowpass_hz} Hz")
        if self.hum_hz:
            parts.append(f"{self.hum_hz} Hz hum notch")
        if self.denoise != "off":
            parts.append(f"{DENOISERS[self.denoise].split(' (')[0]} noise reduction ({self.denoise_strength})")
        if self.presence_db:
            parts.append(f"presence {self.presence_db:+g} dB")
        if self.leveler != "off":
            parts.append(self.leveler)
        if self.normalize:
            parts.append("normalised to -16 LUFS")
        if self.gain_db:
            parts.append(f"{self.gain_db:+g} dB gain")
        return ", ".join(parts + ["limiter"])

    def filter_chain(self, rate: int = 48000, channels: int = 1) -> str:
        f: list[str] = []
        # 1. Channel: pick/combine before anything else (everything after is mono).
        if channels >= 2 and self.channel == "left":
            f.append("pan=mono|c0=c0")
        elif channels >= 2 and self.channel == "right":
            f.append("pan=mono|c0=c1")
        elif channels >= 2 and self.channel == "diff":
            f.append("pan=mono|c0=c0-c1")
        else:
            f.append("aformat=channel_layouts=mono")
        # 2. Repair damage before filters that would smear it.
        if self.declip:
            f.append("adeclip")
        if self.declick:
            f.append("adeclick")
        # 3. Frequency shaping.
        if self.highpass_hz:
            f.append(f"highpass=f={self.highpass_hz}")
        if self.lowpass_hz and self.lowpass_hz < rate / 2:
            f.append(f"lowpass=f={self.lowpass_hz}")
        if self.hum_hz:
            f += [f"bandreject=f={self.hum_hz * k}:width_type=q:w=30"
                  for k in range(1, 7) if self.hum_hz * k < rate / 2]
        # 4. Noise reduction, with its fixed delay trimmed back off.
        if self.denoise != "off":
            opt = _STRENGTH[self.denoise][self.denoise_strength]
            f.append(f"arnndn=m={RNNOISE_MODEL}:{opt}" if self.denoise == "rnnoise" else opt)
            delay = round(DENOISE_DELAY[self.denoise] * rate)
            if delay:
                f += [f"atrim=start_sample={delay}", "asetpts=PTS-STARTPTS", f"apad=pad_len={delay}"]
        # 5. Clarity, then level.
        if self.presence_db:
            f.append(f"equalizer=f=3000:t=q:w=1:g={self.presence_db}")
        if self.leveler == "speechnorm":
            f.append("speechnorm=e=6.25:r=0.00001:l=1")
        elif self.leveler == "dynaudnorm":
            f.append("dynaudnorm=f=150:g=15")
        if self.normalize:
            f.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if self.gain_db:
            f.append(f"volume={self.gain_db}dB")
        f.append("alimiter=limit=0.95:latency=1")  # latency=1: compensate look-ahead
        return ",".join(f)


PRESETS: dict[str, CleanupSettings] = {
    "Normalize only": CleanupSettings(),
    "Gentle (good for transcription)": CleanupSettings(highpass_hz=80, leveler="speechnorm"),
    "Rumble & hiss": CleanupSettings(highpass_hz=120, lowpass_hz=7000, denoise="fft"),
    "Telephone (200 Hz – 4 kHz)": CleanupSettings(highpass_hz=200, lowpass_hz=4000, presence_db=3),
    "Quiet far speaker": CleanupSettings(highpass_hz=100, presence_db=4, leveler="speechnorm",
                                         denoise="fft", denoise_strength="light"),
    "Hum removal (60 Hz)": CleanupSettings(highpass_hz=70, hum_hz=60),
    "Heavy noise (RNNoise)": CleanupSettings(highpass_hz=100, denoise="rnnoise", denoise_strength="strong",
                                             leveler="speechnorm"),
}


def ensure_rnnoise_model(progress: ProgressFn | None = None) -> Path:
    if RNNOISE_MODEL.exists():
        return RNNOISE_MODEL
    import urllib.request
    RNNOISE_MODEL.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(None, "Downloading RNNoise model (~300 KB, first use only)…")
    log.info("Downloading RNNoise model from %s", RNNOISE_URL)
    tmp = RNNOISE_MODEL.with_suffix(".part")
    urllib.request.urlretrieve(RNNOISE_URL, tmp)
    tmp.replace(RNNOISE_MODEL)
    return RNNOISE_MODEL


def channels(path: str | Path) -> int:
    out = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                "-of", "csv=p=0", str(path)])
    return int(out.stdout.strip() or 1)


def _cleanup_args(src: Path, settings: CleanupSettings, out: Path, samples: int,
                  start: float | None = None, length: float | None = None) -> list[str]:
    rate = sample_rate(src)
    if settings.denoise == "rnnoise":
        ensure_rnnoise_model()
    seek = (["-ss", f"{start:.3f}"] if start else []) + (["-t", f"{length:.3f}"] if length else [])
    # RNNoise pads its output to whole 480-sample frames: trim back to the exact
    # source length so every derived file has the playback copy's sample count.
    # (Resample first: loudnorm runs at 192 kHz internally, and atrim counts samples.)
    chain = f"{settings.filter_chain(rate, channels(src))},aresample={rate},atrim=end_sample={samples}"
    # -ar: loudnorm otherwise outputs 192 kHz. First audio stream only.
    return [*seek, "-i", str(src), "-map", "0:a:0", "-af", chain,
            "-ar", str(rate), "-ac", "1", "-sample_fmt", "s16", "-c:a", "flac", "-f", "flac", str(out)]


def _sample_count(src: Path) -> int:
    """Exact decoded length of `src` at its own rate (from its FLAC playback copy)."""
    import soundfile as sf
    return sf.info(str(make_playback_copy(src))).frames


def process(src: str | Path, dst: str | Path, settings: CleanupSettings,
            progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> Path:
    """Whole-file cleanup of the *original* `src` into `dst`: mono FLAC, same rate and length."""
    src = Path(src)
    if settings.denoise == "rnnoise":
        ensure_rnnoise_model(progress)
    log.info("Cleanup: %s", settings.describe())
    samples = _sample_count(src)
    return _write_derived(src, Path(dst), lambda tmp: _cleanup_args(src, settings, tmp, samples),
                          "Processing…", progress, cancel)


def render_clip(src: str | Path, dst: str | Path, settings: CleanupSettings, start: float, length: float,
                cancel: threading.Event | None = None) -> Path:
    """Process just [start, start+length) of `src` — for auditioning a selection."""
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + ".part")
    samples = round(length * sample_rate(src))
    try:
        _run_ffmpeg(_cleanup_args(src, settings, tmp, samples, start, length), length, "Rendering audition…",
                    None, cancel)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dst)
    return dst


# ---------------------------------------------------------------- edit plan render

PLAN_XFADE = 0.020  # seconds of crossfade at each block edge (no clicks at the splice)
PLAN_LEAD_IN = 1.0  # seconds rendered before a block so filters have settled by its start
PLAN_TAIL = 0.5


def plan_blocks(plan: dict | None) -> list[tuple[float, float, CleanupSettings]]:
    return sorted((float(b["start"]), float(b["end"]), CleanupSettings.from_dict(b["settings"]))
                  for b in (plan or {}).get("blocks", []))


def plan_whole(plan: dict | None) -> CleanupSettings | None:
    w = (plan or {}).get("whole")
    return CleanupSettings.from_dict(w) if w else None


def describe_plan(plan: dict | None) -> str:
    parts = []
    if (w := plan_whole(plan)) is not None:
        parts.append(f"whole file: {w.describe()}")
    for i, (s, e, st) in enumerate(plan_blocks(plan), 1):
        parts.append(f"block {i} {s:.2f}-{e:.2f}s: {st.describe()}")
    return "; ".join(parts) or "no cleanup"


def render_plan(src: str | Path, dst: str | Path, plan: dict, progress: ProgressFn | None = None,
                cancel: threading.Event | None = None) -> Path:
    """Render the edit plan: whole-file settings as the base (or the untouched original), with
    each block's own processing spliced in at its time range. Mono FLAC, same rate and length
    as the playback copy, stamped as derived from `src`."""
    import tempfile

    import numpy as np
    import soundfile as sf

    src, dst = Path(src), Path(dst)
    blocks, whole = plan_blocks(plan), plan_whole(plan)
    log.info("Render plan: %s", describe_plan(plan))
    with tempfile.TemporaryDirectory(prefix="ts-plan-") as tmp:
        tmp = Path(tmp)
        steps = len(blocks) + 2

        def sub(i):  # progress for sub-step i of `steps`
            return lambda f=None, d="", **k: progress and progress(None if f is None else (i + f) / steps, d)

        base = process(src, tmp / "base.flac", whole, sub(0), cancel) if whole else make_playback_copy(src)
        rate = sf.info(str(base)).samplerate
        total = sf.info(str(base)).frames
        clips = []
        for i, (s, e, st) in enumerate(blocks, 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if progress:
                progress(i / steps, f"Rendering block {i} of {len(blocks)}…")
            c0 = max(0.0, s - PLAN_LEAD_IN)
            length = min(total / rate, e + PLAN_TAIL) - c0
            clip = render_clip(src, tmp / f"block{i}.flac", st, c0, length, cancel)
            clips.append((round(s * rate), round(e * rate), round(c0 * rate), clip))

        xf = max(1, round(PLAN_XFADE * rate))
        out_tmp = dst.with_name(dst.name + ".part")
        chunk = rate * 10
        try:
            with sf.SoundFile(str(base)) as fin, sf.SoundFile(str(out_tmp), "w", rate, 1, "PCM_16",
                                                              format="FLAC") as fout:
                pos = 0
                while pos < total:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    data = fin.read(min(chunk, total - pos), dtype="float32")
                    end = pos + len(data)
                    for b0, b1, c0, clip in clips:
                        o0, o1 = max(pos, b0), min(end, b1)
                        if o0 >= o1:
                            continue
                        with sf.SoundFile(str(clip)) as cf:
                            cf.seek(o0 - c0)
                            proc = cf.read(o1 - o0, dtype="float32")
                        if len(proc) < o1 - o0:  # clip ended early (end of file): keep the base
                            proc = np.concatenate([proc, data[o0 - pos + len(proc):o1 - pos]])
                        t = np.arange(o0, o1)
                        # Weight of the processed audio: ramps 0->1 over the block's first xf samples
                        # and 1->0 over its last xf samples.
                        w = np.clip(np.minimum((t - b0 + 1) / xf, (b1 - t) / xf), 0.0, 1.0).astype(np.float32)
                        seg = slice(o0 - pos, o1 - pos)
                        data[seg] = data[seg] * (1 - w) + proc * w
                    fout.write(data)
                    pos = end
                    if progress:
                        progress((len(blocks) + 1 + pos / total) / steps, "Splicing blocks into the file…")
        except BaseException:
            out_tmp.unlink(missing_ok=True)
            raise
    st = src.stat()
    os.utime(out_tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    out_tmp.replace(dst)
    return dst


# Back-compat names (CLI, older call sites).
BoostSettings = CleanupSettings
boost = process


def loudness_stats(path: str | Path) -> dict[str, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) via ffmpeg's ebur128."""
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
                           "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True)
    text = proc.stderr.decode(errors="replace")
    summary = text[text.rfind("Summary:"):]
    stats = {}
    for line in summary.splitlines():
        parts = line.split()
        if line.strip().startswith("I:"):
            stats["integrated_lufs"] = float(parts[1])
        elif line.strip().startswith("Peak:"):
            stats["true_peak_dbtp"] = float(parts[1])
    return stats


# ---------------------------------------------------------------- file details

def probe(path: str | Path) -> dict:
    """File specs for the export header: container, codec, rate, channels, size, tags."""
    import json
    out = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    d = json.loads(out.stdout)
    fmt = d.get("format", {})
    stream = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    tags.update({k.lower(): v for k, v in (stream.get("tags") or {}).items() if k.lower() not in tags})

    def num(x, cast=float):
        try:
            return cast(x)
        except (TypeError, ValueError):
            return None

    device = None
    if tags.get("com.android.version"):
        device = f"Android {tags['com.android.version']}"
    for key in ("com.apple.quicktime.model", "model", "device"):
        if tags.get(key):
            device = tags[key]
    return {
        "file": Path(path).name,
        "container": fmt.get("format_long_name") or fmt.get("format_name"),
        "codec": stream.get("codec_long_name") or stream.get("codec_name"),
        "codec_name": stream.get("codec_name"),
        "codec_profile": stream.get("profile"),
        "sample_rate": num(stream.get("sample_rate"), int),
        "channels": num(stream.get("channels"), int),
        "channel_layout": stream.get("channel_layout"),
        "bit_rate": num(stream.get("bit_rate") or fmt.get("bit_rate"), int),
        "size_bytes": num(fmt.get("size"), int),
        "duration": num(fmt.get("duration")),
        "created": tags.get("creation_time") or tags.get("date"),
        "device": device,
        "title_tag": tags.get("title"),
    }
