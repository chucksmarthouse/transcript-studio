"""Step-based progress reporting shared by the pipeline, backends and UI.

A ``Reporter`` is itself a ProgressFn — ``reporter(fraction, detail, **info)``
— so lower layers just call ``progress(...)``. ``fraction=None`` means "busy,
length unknown" (the UI shows an animated bar instead of a stuck 0%).
Extra ``info`` (e.g. ``audio_done=``, ``last_text=``) feeds the stats panel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .logs import log


class ProgressFn(Protocol):
    def __call__(self, fraction: float | None, detail: str = "", **info) -> None: ...


def null_progress(fraction=None, detail="", **info) -> None:
    pass


@dataclass
class ProgressEvent:
    step: int  # 1-based
    steps: int
    name: str
    fraction: float | None
    detail: str = ""
    info: dict = field(default_factory=dict)
    step_elapsed: float = 0.0
    total_elapsed: float = 0.0


class Reporter:
    def __init__(self, emit: Callable[[ProgressEvent], None], steps: list[str]):
        self.emit, self.steps = emit, steps
        self.index = 0
        self.name = ""
        self.t0 = self.t_step = time.monotonic()
        self._last_detail = ""

    def step(self, name: str) -> None:
        self._close_step()
        self.index = self.steps.index(name) + 1
        self.name, self.t_step = name, time.monotonic()
        log.info("Step %d/%d: %s", self.index, len(self.steps), name)
        self(None, "")

    def __call__(self, fraction: float | None = None, detail: str = "", **info) -> None:
        if detail and detail != self._last_detail:
            log.debug("  %s", detail)
            self._last_detail = detail
        now = time.monotonic()
        self.emit(ProgressEvent(self.index, len(self.steps), self.name,
                                None if fraction is None else max(0.0, min(fraction, 1.0)),
                                detail, info, now - self.t_step, now - self.t0))

    def finish(self) -> float:
        self._close_step()
        total = time.monotonic() - self.t0
        log.info("Finished in %.1fs", total)
        return total

    def _close_step(self) -> None:
        if self.name:
            log.info("  %s done in %.1fs", self.name, time.monotonic() - self.t_step)
            self.name = ""
