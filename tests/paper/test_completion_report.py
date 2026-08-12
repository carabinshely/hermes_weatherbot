from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_completion_report_covers_required_paper_scenarios() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/paper_fixture_report.py"],
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    scenarios = report["scenarios"]
    assert set(scenarios) == {
        "winning",
        "losing",
        "voided",
        "rejected",
        "depth_limited",
    }
    assert scenarios["winning"]["entry_status"] == "filled"
    assert scenarios["losing"]["entry_status"] == "filled"
    assert scenarios["voided"]["entry_status"] == "filled"
    assert scenarios["rejected"]["entry_status"] == "execution_rejected"
    assert scenarios["depth_limited"]["entry_status"] == "partial_fill"
    for scenario in scenarios.values():
        assert scenario["integrity"] == "verified"
        assert scenario["state"]["reserved_cash"] == "0"
