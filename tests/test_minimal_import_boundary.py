from __future__ import annotations

import importlib
import subprocess
import sys


def test_safe_packages_do_not_load_live_modules() -> None:
    live_modules = {"eth_account", "polymarket", "web3"}
    before = live_modules.intersection(sys.modules)

    importlib.import_module("weatherbot.domain")
    importlib.import_module("weatherbot.forecasting")
    importlib.import_module("weatherbot.markets")
    importlib.import_module("weatherbot.persistence")
    importlib.import_module("weatherbot.polymarket")
    importlib.import_module("weatherbot.resolution")

    after = live_modules.intersection(sys.modules)
    assert after == before


def test_paper_imports_in_a_cold_process() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import weatherbot.paper"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
