from __future__ import annotations

from pathlib import Path

import pytest

from weatherbot.forecasting import CalibrationUnavailable
from weatherbot.producer import cli


def test_missing_calibration_fails_public_scan_before_candidate_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def unavailable() -> object:
        events.append("calibration")
        raise CalibrationUnavailable("fixture has no accepted artifact")

    def forbidden(*_args: object, **_kwargs: object) -> object:
        events.append("scan")
        raise AssertionError("candidate acquisition must not run without calibration")

    monkeypatch.setattr(cli, "_load_runtime", unavailable)
    monkeypatch.setattr(cli, "collect_calibrated_candidates", forbidden)

    policy = cli.load_producer_policy(cli.REPOSITORY_ROOT)
    emitted, errors = cli.scan_once(policy)

    assert emitted == 0
    assert len(errors) == 1
    assert events == ["calibration"]
    assert "failed closed" in errors[0]


def test_public_entrypoint_contains_no_legacy_probability_or_execution_primitives() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    for forbidden in (
        "SIGMA_F",
        "get_sigma",
        "bucket_prob",
        "calc_kelly",
        "get_adjusted_kelly",
        "get_adjusted_ev_floor",
        "bet_size",
        "ensure_approvals",
        "place_buy_order",
        "submit_scanner_candidate",
        "bot_v3_legacy",
    ):
        assert forbidden not in source


def test_public_producer_does_not_import_internal_paper_package() -> None:
    producer_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("weatherbot/producer").glob("*.py")
    )
    assert "weatherbot.paper" not in producer_sources
    assert "bot_v3_legacy" not in producer_sources
    assert "execution_modes" not in producer_sources
