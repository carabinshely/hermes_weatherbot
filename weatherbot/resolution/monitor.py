"""A monitor loop that performs resolution work on every cycle."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from weatherbot.resolution.model import ResolutionCycleReport


class ResolutionCycleRunner(Protocol):
    def run_once(self) -> ResolutionCycleReport: ...


@dataclass(slots=True)
class ResolutionMonitor:
    worker: ResolutionCycleRunner
    interval_seconds: float = 600.0
    sleep: Callable[[float], None] = time.sleep
    on_report: Callable[[ResolutionCycleReport], None] | None = None

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("resolution monitor interval must be positive")

    def run_once(self) -> ResolutionCycleReport:
        report = self.worker.run_once()
        if self.on_report is not None:
            self.on_report(report)
        return report

    def run_forever(self, *, max_cycles: int | None = None) -> None:
        if max_cycles is not None and max_cycles <= 0:
            raise ValueError("max_cycles must be positive when supplied")
        completed = 0
        while max_cycles is None or completed < max_cycles:
            self.run_once()
            completed += 1
            if max_cycles is None or completed < max_cycles:
                self.sleep(self.interval_seconds)
