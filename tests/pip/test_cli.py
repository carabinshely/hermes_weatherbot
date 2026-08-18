from __future__ import annotations

from pathlib import Path

import pytest

from weatherbot.pip import PipExporterConfig, PipOutbox
from weatherbot.pip import cli


def _disabled_config(tmp_path: Path) -> PipExporterConfig:
    return PipExporterConfig(
        enabled=False,
        endpoint="",
        outbox_path=tmp_path / "outbox.sqlite3",
        signing_key_path=None,
        key_id=None,
    )


def test_status_reports_uninitialized_disabled_exporter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _disabled_config(tmp_path)
    monkeypatch.setattr(cli, "_config", lambda: config)

    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "enabled: false" in output
    assert "outbox state: not initialized" in output


def test_status_reports_initialized_outbox_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _disabled_config(tmp_path)
    with PipOutbox(config.outbox_path):
        pass
    monkeypatch.setattr(cli, "_config", lambda: config)

    assert cli.status() == 0
    output = capsys.readouterr().out
    assert "staged_intents: 0" in output
    assert "pending: 0" in output
    assert "acknowledged: 0" in output
    assert "oldest unacknowledged: none" in output


def test_disabled_reconcile_and_delivery_are_noops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _disabled_config(tmp_path)
    monkeypatch.setattr(cli, "_config", lambda: config)

    assert cli.main(["reconcile"]) == 0
    assert cli.main(["deliver-once"]) == 0
    stderr = capsys.readouterr().err
    assert "reconciliation skipped" in stderr
    assert "delivery skipped" in stderr


def test_operator_retry_and_worker_fail_closed_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _disabled_config(tmp_path)
    monkeypatch.setattr(cli, "_config", lambda: config)

    assert (
        cli.main(
            [
                "retry-dead-letter",
                "--event-id",
                "event-1",
                "--operator",
                "operator-1",
                "--reason",
                "manual retry",
            ]
        )
        == 2
    )
    assert cli.main(["run", "--interval-seconds", "0"]) == 2
    assert "PIP export is disabled" in capsys.readouterr().err


def test_dead_letter_command_reports_missing_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _disabled_config(tmp_path)
    monkeypatch.setattr(cli, "_config", lambda: config)

    assert (
        cli.main(
            [
                "dead-letter",
                "--event-id",
                "event-1",
                "--operator",
                "operator-1",
                "--reason",
                "manual hold",
            ]
        )
        == 2
    )
    assert "outbox has not been initialized" in capsys.readouterr().err
