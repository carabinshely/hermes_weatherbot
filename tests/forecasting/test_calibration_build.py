from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from weatherbot.forecasting.archive import (
    PRODUCTION_FORECAST_CONTRACT_ID,
    SINGLE_RUN_CAPTURE_CONTRACT_ID,
)
from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_build import (
    DEFAULT_MARKETS,
    OBSERVATION_CONTRACT_ID,
    ImmutableHttpCache,
    collect_calibration_dataset,
    parity_report_from_evidence,
)

_PARITY_EVIDENCE = Path("tests/fixtures/forecasting/ecmwf_single_run_parity_2026-04-18.json")


def _write_cache_entry(
    root: Path,
    *,
    namespace: str,
    key: str,
    requested_url: str,
    final_url: str,
    payload: bytes,
    retrieved_at: datetime,
    suffix: str = ".json",
) -> tuple[Path, Path]:
    payload_path = root / namespace / f"{key}{suffix}"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_bytes(payload)
    metadata_path = payload_path.with_name(f"{payload_path.name}.meta.json")
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "requested_url": requested_url,
                "final_url": final_url,
                "retrieved_at_utc": retrieved_at.isoformat(),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return payload_path, metadata_path


def test_default_market_registry_matches_current_settlement_contracts() -> None:
    expected = {
        "nyc": ("KLGA", "America/New_York", "new-york-city"),
        "chicago": ("KORD", "America/Chicago", "chicago"),
        "miami": ("KMIA", "America/New_York", "miami"),
        "dallas": ("KDAL", "America/Chicago", "dallas"),
        "seattle": ("KSEA", "America/Los_Angeles", "seatac"),
        "atlanta": ("KATL", "America/New_York", "atlanta"),
    }

    assert len(DEFAULT_MARKETS) == len(expected)
    assert {market.city for market in DEFAULT_MARKETS} == set(expected)
    assert len({market.station_id for market in DEFAULT_MARKETS}) == len(DEFAULT_MARKETS)
    for market in DEFAULT_MARKETS:
        station, timezone, path_city = expected[market.city]
        assert market.station_id == station
        assert market.market_timezone == timezone
        assert f"/{path_city}/{station}" in market.resolution_source_url
        assert market.history_url(date(2026, 6, 8)).endswith("/date/2026-06-08")


def test_committed_archive_parity_evidence_builds_compatible_gate() -> None:
    report = parity_report_from_evidence(_PARITY_EVIDENCE)

    assert report.reference_contract_id == PRODUCTION_FORECAST_CONTRACT_ID
    assert report.candidate_contract_id == SINGLE_RUN_CAPTURE_CONTRACT_ID
    assert report.reference_count == 18
    assert report.matched_count == 18
    assert report.reference_coverage == pytest.approx(1.0)
    assert report.mae_f == pytest.approx(0.3055555555555556)
    assert report.max_abs_error_f == pytest.approx(0.5)
    assert report.compatible


def test_offline_cache_replays_exact_frozen_bytes_and_metadata(tmp_path: Path) -> None:
    payload = b'{"value":42}'
    requested_url = "https://example.test/request"
    final_url = "https://example.test/final"
    retrieved_at = datetime(2026, 8, 1, 12, 34, tzinfo=UTC)
    _write_cache_entry(
        tmp_path,
        namespace="forecasts/nyc",
        key="2026-08-01",
        requested_url=requested_url,
        final_url=final_url,
        payload=payload,
        retrieved_at=retrieved_at,
    )
    cache = ImmutableHttpCache(root=tmp_path, offline=True)

    capture = cache.get(
        namespace="forecasts/nyc",
        key="2026-08-01",
        requested_url=requested_url,
        suffix=".json",
        headers={},
    )

    assert capture.payload == payload
    assert capture.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert capture.requested_url == requested_url
    assert capture.final_url == final_url
    assert capture.retrieved_at_utc == retrieved_at


def test_offline_cache_rejects_partial_entry(tmp_path: Path) -> None:
    payload_path = tmp_path / "forecasts" / "nyc" / "2026-08-01.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(b"{}")
    cache = ImmutableHttpCache(root=tmp_path, offline=True)

    with pytest.raises(CalibrationError, match="partial cache entry"):
        cache.get(
            namespace="forecasts/nyc",
            key="2026-08-01",
            requested_url="https://example.test/request",
            suffix=".json",
            headers={},
        )


def test_offline_cache_rejects_url_contract_change(tmp_path: Path) -> None:
    _write_cache_entry(
        tmp_path,
        namespace="observations/nyc",
        key="2026-08-01",
        requested_url="https://example.test/original",
        final_url="https://example.test/original",
        payload=b"<html></html>",
        retrieved_at=datetime(2026, 8, 2, tzinfo=UTC),
        suffix=".html",
    )
    cache = ImmutableHttpCache(root=tmp_path, offline=True)

    with pytest.raises(CalibrationError, match="requested URL differs"):
        cache.get(
            namespace="observations/nyc",
            key="2026-08-01",
            requested_url="https://example.test/changed",
            suffix=".html",
            headers={},
        )


def test_dataset_builder_rejects_unfinalized_target_range_before_network(tmp_path: Path) -> None:
    report = parity_report_from_evidence(_PARITY_EVIDENCE)
    cache = ImmutableHttpCache(root=tmp_path, offline=True)

    with pytest.raises(CalibrationError, match="too recent"):
        collect_calibration_dataset(
            start_date=date(2026, 8, 11),
            end_date=date(2026, 8, 11),
            cache=cache,
            markets=(DEFAULT_MARKETS[0],),
            parity_report=report,
            now_utc=datetime(2026, 8, 12, 12, tzinfo=UTC),
        )


def test_observation_contract_is_explicit_and_versioned() -> None:
    assert OBSERVATION_CONTRACT_ID.endswith(":v1")
    assert "wunderground" in OBSERVATION_CONTRACT_ID
    assert "whole-degree-f" in OBSERVATION_CONTRACT_ID
