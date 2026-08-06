from __future__ import annotations

from datetime import timedelta

from tests.resolution.helpers import NOW
from weatherbot.resolution import ResolutionCycleReport, ResolutionMonitor


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> ResolutionCycleReport:
        self.calls += 1
        at = NOW + timedelta(seconds=self.calls)
        return ResolutionCycleReport.empty(at)


def test_monitor_performs_resolution_work_before_sleeping() -> None:
    runner = FakeRunner()
    sleeps: list[float] = []
    reports: list[ResolutionCycleReport] = []
    monitor = ResolutionMonitor(
        worker=runner,
        interval_seconds=12.5,
        sleep=sleeps.append,
        on_report=reports.append,
    )
    monitor.run_forever(max_cycles=3)
    assert runner.calls == 3
    assert len(reports) == 3
    assert sleeps == [12.5, 12.5]


def test_monitor_one_shot_runs_worker() -> None:
    runner = FakeRunner()
    report = ResolutionMonitor(worker=runner).run_once()
    assert runner.calls == 1
    assert report.checked == 0
