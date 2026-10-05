"""Live system stats for the progress panel: GPU busy/VRAM (AMD sysfs or nvidia-smi), CPU, RAM."""

from __future__ import annotations

import glob
import shutil
import subprocess
from pathlib import Path


class SystemStats:
    def __init__(self):
        self._amd = self._find_amd()
        self._nvidia = shutil.which("nvidia-smi")
        self._cpu_prev: tuple[int, int] | None = None

    @staticmethod
    def _find_amd() -> Path | None:
        for busy in sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")):
            return Path(busy).parent
        return None

    def sample(self) -> dict:
        """Current readings; keys are absent when a value isn't available."""
        out: dict = {}
        cpu = self._cpu_percent()
        if cpu is not None:
            out["cpu_percent"] = cpu
        mem = _meminfo()
        if mem:
            out["ram_used_gb"], out["ram_total_gb"] = mem
        if self._nvidia:
            try:
                r = subprocess.run([self._nvidia, "--query-gpu=utilization.gpu,memory.used,memory.total",
                                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
                busy, used, total = (float(x) for x in r.stdout.splitlines()[0].split(","))
                out.update(gpu_percent=busy, vram_used_gb=used / 1024, vram_total_gb=total / 1024)
                return out
            except Exception:
                pass
        if self._amd:
            try:
                out["gpu_percent"] = float((self._amd / "gpu_busy_percent").read_text())
                out["vram_used_gb"] = int((self._amd / "mem_info_vram_used").read_text()) / 2**30
                out["vram_total_gb"] = int((self._amd / "mem_info_vram_total").read_text()) / 2**30
            except Exception:
                pass
        return out

    def _cpu_percent(self) -> float | None:
        try:
            fields = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        except Exception:
            return None
        idle, total = fields[3] + fields[4], sum(fields)
        prev, self._cpu_prev = self._cpu_prev, (idle, total)
        if prev is None or total == prev[1]:
            return None
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))


def _meminfo() -> tuple[float, float] | None:
    try:
        info = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
        return (info["MemTotal"] - info["MemAvailable"]) / 2**20, info["MemTotal"] / 2**20
    except Exception:
        return None
