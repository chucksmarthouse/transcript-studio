"""Hugging Face model downloads with byte-level progress (public, ungated repos — no token).

huggingface_hub's own progress bars go to the terminal (and faster-whisper
disables them), so this watches the cache folder grow against the repo's
file sizes and reports through a ProgressFn.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from pathlib import Path

from .logs import log
from .progress import ProgressFn


def _cache_bytes(repo_id: str) -> int:
    from huggingface_hub.constants import HF_HUB_CACHE
    blobs = Path(HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}" / "blobs"
    return sum(f.stat().st_size for f in blobs.glob("*") if f.is_file()) if blobs.exists() else 0


def download(repo_id: str, what: str, progress: ProgressFn, allow_patterns: list[str] | None = None) -> str:
    """Local snapshot path of `repo_id`, downloading (with progress) only if not cached."""
    from huggingface_hub import HfApi, snapshot_download

    try:  # fully offline when already cached
        path = snapshot_download(repo_id, allow_patterns=allow_patterns, local_files_only=True)
        log.info("%s: using cached %s", what, path)
        return path
    except Exception:
        pass

    total = 0
    try:
        info = HfApi().model_info(repo_id, files_metadata=True)
        total = sum(s.size or 0 for s in info.siblings
                    if not allow_patterns or any(fnmatch.fnmatch(s.rfilename, p) for p in allow_patterns))
    except Exception as e:
        log.warning("%s: couldn't get download size (%s); progress will be indeterminate", what, e)
    log.info("%s: downloading %s (%.0f MB) from Hugging Face", what, repo_id, total / 2**20)

    result: dict = {}

    def worker():
        try:
            result["path"] = snapshot_download(repo_id, allow_patterns=allow_patterns)
        except Exception as e:  # re-raised on the calling thread
            result["error"] = e

    base = _cache_bytes(repo_id)
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t0 = time.monotonic()
    while t.is_alive():
        t.join(0.5)
        done = max(0, _cache_bytes(repo_id) - base)
        rate = done / max(time.monotonic() - t0, 1e-6) / 2**20
        if total:
            progress(done / total, f"Downloading {what}: {done / 2**20:.0f} / {total / 2**20:.0f} MB "
                     f"({rate:.1f} MB/s, first use only)")
        else:
            progress(None, f"Downloading {what}: {done / 2**20:.0f} MB (first use only)")
    if "error" in result:
        raise result["error"]
    log.info("%s: downloaded in %.1fs", what, time.monotonic() - t0)
    return result["path"]
