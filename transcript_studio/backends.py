"""Transcription backends.

A machine with an AMD (Polaris) GPU cannot use faster-whisper/CTranslate2 (CUDA only),
so there are three engines behind one interface:

- ``faster-whisper`` on CUDA  — the spec's intended path; picked automatically
  as soon as an NVIDIA GPU + CUDA libraries are present.
- ``whisper.cpp`` on Vulkan   — uses the AMD card today (vendor/whisper.cpp,
  built with -DGGML_VULKAN=ON).
- ``faster-whisper`` on CPU   — last-resort fallback (int8).

All produce the same ``RawSegment`` / ``Word`` output. Each backend has a
``prepare`` phase (download/load the model) and a ``transcribe`` phase so the
UI can show them as separate steps.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import hub
from .audio import Cancelled
from .logs import log
from .model import Word
from .progress import ProgressFn

ROOT = Path(__file__).resolve().parent.parent
WHISPER_CPP_DIR = ROOT / "vendor" / "whisper.cpp"
WHISPER_CPP_BIN = WHISPER_CPP_DIR / "build" / "bin" / "whisper-cli"
WHISPER_CPP_MODELS = WHISPER_CPP_DIR / "models"
GGML_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"  # ungated


@dataclass
class RawSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class TranscribeOptions:
    model_size: str
    language: str | None = None  # None = auto-detect (".en" models force English)
    vad_filter: bool | None = None  # None = backend default
    beam_size: int = 5


class Backend:
    key: str = ""
    label: str = ""
    engine_name: str = ""  # shown in the export's intro line

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def models(self) -> list[str]:
        raise NotImplementedError

    def prepare(self, model_size: str, progress: ProgressFn, cancel: threading.Event) -> None:
        """Download (first use) and load the model."""

    def transcribe(self, wav_path: Path, pcm: np.ndarray, duration: float, opts: TranscribeOptions,
                   progress: ProgressFn, cancel: threading.Event) -> list[RawSegment]:
        raise NotImplementedError


# ---------------------------------------------------------------- faster-whisper

_FW_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


class FasterWhisperBackend(Backend):
    engine_name = "faster-whisper"

    def __init__(self, device: str):
        self.device = device
        self.key = f"faster-whisper-{device}"
        self.label = f"faster-whisper ({'NVIDIA CUDA' if device == 'cuda' else 'CPU'})"
        self.compute_type = "float16" if device == "cuda" else "int8"
        self._model = None
        self._model_size = None

    def available(self):
        try:
            import ctranslate2
        except ImportError as e:
            return False, f"faster-whisper not installed ({e})"
        if self.device == "cuda":
            try:
                n = ctranslate2.get_cuda_device_count()
            except Exception as e:  # missing libcudart etc.
                return False, f"CUDA unavailable: {e}"
            if n == 0:
                return False, "no CUDA (NVIDIA) GPU detected"
        return True, ""

    def models(self):
        from faster_whisper import available_models
        return list(available_models())

    def prepare(self, model_size, progress, cancel):
        if self._model is not None and self._model_size == model_size:
            log.info("faster-whisper %s already loaded on %s", model_size, self.device)
            return
        from faster_whisper import WhisperModel
        from faster_whisper.utils import _MODELS

        repo = model_size if "/" in model_size else _MODELS[model_size]
        path = hub.download(repo, f"Whisper model {model_size}", progress, _FW_FILES)
        if cancel.is_set():
            raise Cancelled()
        progress(None, f"Loading {model_size} into memory ({self.device}, {self.compute_type})…")
        self._model = None  # free the old one (VRAM) before loading the new one
        self._model = WhisperModel(path, device=self.device, compute_type=self.compute_type)
        self._model_size = model_size
        log.info("faster-whisper %s loaded on %s (%s)", model_size, self.device, self.compute_type)

    def transcribe(self, wav_path, pcm, duration, opts, progress, cancel):
        self.prepare(opts.model_size, progress, cancel)
        vad = True if opts.vad_filter is None else opts.vad_filter  # spec §5.3
        log.info("faster-whisper transcribe: model=%s device=%s beam=%d vad=%s language=%s",
                 opts.model_size, self.device, opts.beam_size, vad, opts.language or "auto")
        # Pass the already-decoded 16 kHz samples: faster-whisper's own decoder
        # (PyAV) is version-fragile (PyAV 19 broke faster-whisper 1.2.1's call).
        segments, info = self._model.transcribe(
            pcm, language=opts.language, beam_size=opts.beam_size,
            word_timestamps=True, vad_filter=vad,
        )
        progress(None, "Decoding first segment…")
        out = []
        for seg in segments:  # generator: decoding happens while iterating
            if cancel.is_set():
                raise Cancelled()
            words = [Word(w.word.strip(), float(w.start), float(w.end), float(w.probability))
                     for w in (seg.words or [])]
            out.append(RawSegment(float(seg.start), float(seg.end), seg.text.strip(), words))
            progress(seg.end / max(duration, 1e-6), "Transcribing…", audio_done=seg.end, last_text=seg.text.strip())
        log.info("faster-whisper produced %d segments", len(out))
        return out


# ---------------------------------------------------------------- whisper.cpp

WHISPER_CPP_MODELS_LIST = [
    "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
    "large-v1", "large-v2", "large-v3", "large-v3-turbo",
]
_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)%")
_SEGMENT_RE = re.compile(r"^\[(\d+):(\d\d):(\d\d)\.(\d{3}) --> (\d+):(\d\d):(\d\d)\.(\d{3})\]\s*(.*)")
_NOTABLE_RE = re.compile(r"vulkan|ggml_vk|gpu|device|backend|model size|failed|error", re.I)


def _hms(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


class WhisperCppBackend(Backend):
    key = "whisper.cpp-vulkan"
    label = "whisper.cpp (Vulkan GPU)"
    engine_name = "whisper.cpp"

    def available(self):
        if not WHISPER_CPP_BIN.exists():
            return False, f"whisper.cpp not built ({WHISPER_CPP_BIN} missing)"
        if not list(WHISPER_CPP_BIN.parent.glob("libggml-vulkan.so*")):
            return False, "whisper.cpp built without Vulkan"
        if shutil.which("vulkaninfo"):
            info = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True).stdout
            if not re.search(r"PHYSICAL_DEVICE_TYPE_(DISCRETE|INTEGRATED)_GPU", info):
                return False, "no Vulkan GPU detected"
        return True, ""

    def models(self):
        return list(WHISPER_CPP_MODELS_LIST)

    @staticmethod
    def model_path(name: str) -> Path:
        return WHISPER_CPP_MODELS / f"ggml-{name}.bin"

    def prepare(self, model_size, progress, cancel):
        self.ensure_model(model_size, progress, cancel)

    def ensure_model(self, name: str, progress: ProgressFn, cancel: threading.Event) -> Path:
        path = self.model_path(name)
        if path.exists():
            log.info("whisper.cpp model %s: %s (%.0f MB)", name, path, path.stat().st_size / 2**20)
            return path
        url = GGML_MODEL_URL.format(name=name)
        log.info("whisper.cpp model %s: downloading %s", name, url)
        tmp = path.with_suffix(".bin.part")
        with urllib.request.urlopen(url) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                if cancel.is_set():
                    f.close()
                    tmp.unlink(missing_ok=True)
                    raise Cancelled()
                f.write(chunk)
                done += len(chunk)
                progress(done / total if total else None,
                         f"Downloading Whisper model {name}: {done >> 20} / {total >> 20} MB (first use only)")
        tmp.replace(path)
        log.info("whisper.cpp model %s downloaded (%d MB)", name, done >> 20)
        return path

    def transcribe(self, wav_path, pcm, duration, opts, progress, cancel):
        model = self.ensure_model(opts.model_size, progress, cancel)
        with tempfile.TemporaryDirectory(prefix="ts-wcpp-") as tmp:
            out_base = Path(tmp) / "out"
            cmd = [str(WHISPER_CPP_BIN), "-m", str(model), "-f", str(wav_path),
                   "-ojf", "-of", str(out_base), "-pp", "-bs", str(opts.beam_size),
                   "-t", str(min(8, os.cpu_count() or 4))]
            # whisper.cpp's VAD drops coughs/laughs (Silero calls them non-speech)
            # and leaves token timestamps un-shifted, so it's off unless asked for.
            if opts.vad_filter:
                cmd += ["--vad", "-vm", str(WHISPER_CPP_MODELS / "ggml-silero-v6.2.0.bin")]
            if opts.language:
                cmd += ["-l", opts.language]
            elif not opts.model_size.endswith(".en"):
                cmd += ["-l", "auto"]
            if shutil.which("stdbuf"):  # line-buffer stdout so segments arrive as they're decoded
                cmd = ["stdbuf", "-oL", *cmd]
            log.info("Running: %s", " ".join(cmd))
            progress(None, "Starting whisper.cpp (loading model onto the GPU)…")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors="replace")
            log_tail: list[str] = []
            frac = 0.0
            for line in proc.stdout:
                if cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise Cancelled()
                line = line.rstrip()
                log_tail = (log_tail + [line])[-40:]
                if m := _SEGMENT_RE.match(line):
                    end = _hms(*m.groups()[4:8])
                    frac = max(frac, end / max(duration, 1e-6))
                    log.debug("whisper.cpp: %s", line)
                    progress(frac, "Transcribing (whisper.cpp / Vulkan)…", audio_done=end, last_text=m[9].strip())
                elif m := _PROGRESS_RE.search(line):
                    frac = max(frac, int(m[1]) / 100.0)
                    progress(frac, "Transcribing (whisper.cpp / Vulkan)…")
                elif line:
                    (log.info if _NOTABLE_RE.search(line) else log.debug)("whisper.cpp: %s", line)
            if (rc := proc.wait()) != 0:
                log.error("whisper.cpp exited with %d; last output:\n%s", rc, "\n".join(log_tail))
                raise RuntimeError(f"whisper.cpp failed (exit {rc}):\n" + "\n".join(log_tail[-15:]))
            data = json.loads(out_base.with_suffix(".json").read_text(encoding="utf-8"))
        segments = [seg for seg in map(_wcpp_segment, data["transcription"]) if seg.text]
        log.info("whisper.cpp produced %d segments", len(segments))
        return segments


def _wcpp_segment(seg: dict) -> RawSegment:
    words: list[Word] = []
    probs: list[list[float]] = []
    for tok in seg.get("tokens", []):
        text = tok["text"]
        if text.startswith("[_") or not text:  # [_BEG_], [_TT_123] etc.
            continue
        start, end = tok["offsets"]["from"] / 1000.0, tok["offsets"]["to"] / 1000.0
        if text.startswith(" ") or not words:
            words.append(Word(text.strip(), start, end, 0.0))
            probs.append([tok["p"]])
        else:  # continuation token of the current word
            words[-1].text += text
            words[-1].end_time = end
            probs[-1].append(tok["p"])
    for w, p in zip(words, probs):
        w.probability = sum(p) / len(p)  # same aggregation faster-whisper uses
    words = [w for w in words if w.text]
    return RawSegment(seg["offsets"]["from"] / 1000.0, seg["offsets"]["to"] / 1000.0,
                      seg["text"].strip(), words)


# ---------------------------------------------------------------- selection

BACKENDS: list[Backend] = [
    FasterWhisperBackend("cuda"),
    WhisperCppBackend(),
    FasterWhisperBackend("cpu"),
]


def get_backend(key: str) -> Backend:
    for b in BACKENDS:
        if b.key == key:
            return b
    raise KeyError(key)


def best_backend() -> Backend:
    """First available backend in preference order: CUDA, Vulkan, CPU."""
    for b in BACKENDS:
        if b.available()[0]:
            return b
    raise RuntimeError("No transcription backend available")
