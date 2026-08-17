from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "bot_v3.py"), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_public_cli_exposes_signal_commands_only() -> None:
    completed = _run("--help")
    assert completed.returncode == 0
    assert "{scan,run,status}" in completed.stdout
    for forbidden in ("--mode", "--confirm-live", "cancel", "paper-reset"):
        assert forbidden not in completed.stdout


def test_public_cli_rejects_old_mode_flag() -> None:
    completed = _run("scan", "--mode", "paper")
    assert completed.returncode == 2
    assert "unrecognized arguments" in completed.stderr


def test_internal_paper_cli_has_distinct_experiment_only_surface() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "weatherbot.paper", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0
    assert "deterministic internal PAPER strategy experiments" in completed.stdout
    assert "{evaluate}" in completed.stdout
    for obsolete in ("scan", "run", "status", "resolve", "reset"):
        assert obsolete not in completed.stdout
