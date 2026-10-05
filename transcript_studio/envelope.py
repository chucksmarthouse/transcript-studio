"""Audio envelope (peak per 10 ms) for the waveform display."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import numpy as np

from .audio import Cancelled, duration
from .logs import log
from .progress import ProgressFn, null_progress

ENV_RATE = 100  # envelope values per second
_DECODE_RATE = 8000
_HOP = _DECODE_RATE // ENV_RATE


def compute_envelope(path: str | Path, progress: ProgressFn = null_progress,
                     cancel: threading.Event | None = None) -> np.ndarray:
    """Peak |amplitude| (0..1) per 1/ENV_RATE s, decoded in streaming chunks."""
    total = duration(path)
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
           "-ac", "1", "-ar", str(_DECODE_RATE), "-f", "s16le", "-"]
    log.debug("Envelope: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunk_bytes = _DECODE_RATE * 2 * 10  # 10 s
    peaks, carry, done = [], np.zeros(0, dtype=np.int16), 0.0
    while True:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled()
        buf = proc.stdout.read(chunk_bytes)
        if not buf:
            break
        samples = np.concatenate([carry, np.frombuffer(buf[:len(buf) // 2 * 2], dtype=np.int16)])
        n = len(samples) // _HOP * _HOP
        if n:
            peaks.append(np.abs(samples[:n].astype(np.int32)).reshape(-1, _HOP).max(axis=1))
        carry = samples[n:]
        done += len(buf) / 2 / _DECODE_RATE
        progress(min(done / max(total, 1e-6), 1.0), "Drawing waveform…")
    if len(carry):
        peaks.append(np.array([np.abs(carry.astype(np.int32)).max()]))
    proc.wait()
    env = (np.concatenate(peaks) if peaks else np.zeros(0)).astype(np.float32) / 32768.0
    log.info("Envelope: %d values (%.1fs) for %s", len(env), len(env) / ENV_RATE, Path(path).name)
    return env
