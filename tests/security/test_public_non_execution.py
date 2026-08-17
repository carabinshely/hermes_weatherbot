from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_non_execution_guard_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/check_public_non_execution.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_public_entrypoint_cold_import_does_not_load_quarantined_modules() -> None:
    code = """
import sys
import bot_v3
forbidden = {
    'bot_v3_legacy',
    'bot_v3_legacy_impl',
    'weatherbot.paper',
    'weatherbot.dependencies',
    'weatherbot.polymarket',
    'web3',
    'eth_account',
    'polymarket',
}
loaded = sorted(forbidden.intersection(sys.modules))
if loaded:
    raise SystemExit(f'public import loaded forbidden modules: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
