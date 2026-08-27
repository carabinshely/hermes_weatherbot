from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from weatherbot.pip import PipExportError
from weatherbot.producer import cli
from weatherbot.runtime_control import ShutdownController

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


def _run_module(
    *args: str,
    cwd: Path | None = None,
    remove_env: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for name in remove_env:
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", "weatherbot.producer", *args],
        cwd=cwd or ROOT,
        env=env,
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


def test_module_entrypoint_exposes_same_public_surface() -> None:
    completed = _run_module("--help")
    assert completed.returncode == 0
    assert "{scan,run,status}" in completed.stdout


def test_module_entrypoint_does_not_depend_on_current_working_directory(tmp_path: Path) -> None:
    completed = _run_module("status", cwd=tmp_path)
    assert completed.returncode == 0
    assert "Hermes public producer" in completed.stdout
    assert str(ROOT / "state" / "signals-v1.jsonl") in completed.stdout


def test_status_starts_without_wallet_or_exchange_write_credentials() -> None:
    completed = _run_module(
        "status",
        remove_env=(
            "PK",
            "PRIVATE_KEY",
            "WALLET",
            "WALLET_ADDRESS",
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_API_PASSPHRASE",
        ),
    )
    assert completed.returncode == 0
    assert "Hermes public producer" in completed.stdout


def test_public_cli_rejects_old_mode_flag() -> None:
    completed = _run("scan", "--mode", "paper")
    assert completed.returncode == 2
    assert "unrecognized arguments" in completed.stderr


def test_run_stops_after_current_scan_and_returns_signal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ShutdownController()
    calls = 0

    def scan_once(_policy: object) -> tuple[int, list[str]]:
        nonlocal calls
        calls += 1
        controller.request(signal.SIGTERM)
        return 0, []

    monkeypatch.setattr(cli, "scan_once", scan_once)
    policy = cli.load_producer_policy(cli.REPOSITORY_ROOT)

    assert cli.run_producer(policy, controller=controller) == 143
    assert calls == 1


def test_runtime_does_not_mutate_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ALL_PROXY": "socks5://127.0.0.1:1080",
        "all_proxy": "socks5://127.0.0.1:1081",
        "HTTP_PROXY": "http://127.0.0.1:8080",
        "http_proxy": "http://127.0.0.1:8081",
        "HTTPS_PROXY": "http://127.0.0.1:8443",
        "https_proxy": "http://127.0.0.1:8444",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    controller = ShutdownController()
    controller.request(signal.SIGTERM)
    policy = cli.load_producer_policy(cli.REPOSITORY_ROOT)
    assert cli.run_producer(policy, controller=controller) == 143

    for name, value in values.items():
        assert os.environ[name] == value


def test_pip_staging_failure_does_not_block_local_signal_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = cli.load_producer_policy(cli.REPOSITORY_ROOT)
    candidate = SimpleNamespace(city_name="Chicago", horizon="D+0")

    class Signal:
        def to_mapping(self) -> dict[str, str]:
            return {"signal_id": "signal-1"}

    signal_value = Signal()
    persisted: list[object] = []

    def collect_candidates(**_kwargs: object) -> tuple[list[SimpleNamespace], list[str]]:
        return [candidate], []

    def evaluate(*_args: object, **_kwargs: object) -> tuple[Signal, object]:
        return signal_value, object()

    def load_exporter(_root: Path) -> SimpleNamespace:
        return SimpleNamespace(enabled=True)

    def stage_signal(*_args: object, **_kwargs: object) -> None:
        raise PipExportError("PIP unavailable")

    def append_signal(_path: Path, item: object) -> None:
        persisted.append(item)

    def reconcile(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(cli, "_load_runtime", lambda: object())
    monkeypatch.setattr(cli, "collect_calibrated_candidates", collect_candidates)
    monkeypatch.setattr(cli, "evaluate_candidate", evaluate)
    monkeypatch.setattr(cli, "load_exporter_config", load_exporter)
    monkeypatch.setattr(cli, "stage_signal", stage_signal)
    monkeypatch.setattr(cli, "append_signal", append_signal)
    monkeypatch.setattr(cli, "reconcile_signal_log", reconcile)

    emitted, errors = cli.scan_once(policy)

    assert emitted == 1
    assert persisted == [signal_value]
    assert errors == ["Chicago D+0: PIP staging failed: PIP unavailable"]


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
