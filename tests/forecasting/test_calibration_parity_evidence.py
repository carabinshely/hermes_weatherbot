from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "forecasting"
    / "ecmwf_single_run_parity_2026-04-18.json"
)


def test_persisted_ecmwf_archive_parity_matches_legacy_signal_precision() -> None:
    raw = _FIXTURE.read_bytes()
    evidence = json.loads(raw)
    rows = evidence["rows"]

    assert evidence["archive_run_utc"] == "2026-04-17T18:00:00Z"
    assert evidence["historical_reference_code_commit"] == (
        "d05c077294b95be5557d067546dab49ca24863b5"
    )
    assert evidence["effective_production_contract_id"] != evidence["archive_capture_contract_id"]
    assert len(rows) == 18
    assert len({row["city"] for row in rows}) == 6
    assert {row["horizon"] for row in rows} == {"D+0", "D+1", "D+2"}
    assert len({row["snapshot_file_sha256"] for row in rows}) == 18
    assert len({row["archive_response_sha256"] for row in rows}) == 6

    for row in rows:
        assert round(row["archive_hourly_local_max_f"]) == row["archive_rounded_f"]
        assert row["archive_rounded_f"] == row["production_ecmwf_rounded_f"]
        assert row["rounded_error_f"] == 0
        for field in ("snapshot_file_sha256", "archive_response_sha256"):
            digest = row[field]
            assert len(digest) == 64
            assert set(digest) <= set("0123456789abcdef")

    absolute_errors = [abs(row["raw_error_from_rounded_reference_f"]) for row in rows]
    summary = evidence["summary"]
    assert summary["pairs"] == len(rows)
    assert summary["rounded_exact_matches"] == len(rows)
    assert summary["rounded_exact_fraction"] == pytest.approx(1.0)
    assert sum(absolute_errors) / len(absolute_errors) == pytest.approx(
        summary["mae_vs_rounded_reference_f"]
    )
    assert max(absolute_errors) == pytest.approx(summary["max_abs_vs_rounded_reference_f"])
    assert summary["max_abs_vs_rounded_reference_f"] <= 0.5

    # Keep the small evidence file itself stable and auditable in review output.
    assert (
        hashlib.sha256(raw).hexdigest()
        == "4932e56c94a6794383f488f2e36597ba98985125ab153631792402bbb2144e10"
    )


def test_persisted_00z_counterexample_identifies_18z_as_the_matching_run() -> None:
    evidence = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    alternative = evidence["run_disambiguation"]

    assert alternative["candidate_run_utc"] == "2026-04-18T00:00:00Z"
    assert alternative["city"] == "atlanta"
    assert alternative["pairs"] == 3
    assert alternative["rounded_exact_matches"] == 1
    assert alternative["rounded_exact_fraction"] == pytest.approx(1 / 3)
