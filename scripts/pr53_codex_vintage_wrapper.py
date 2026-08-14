from __future__ import annotations

import runpy
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"wrapper anchor not found in {path}: {old[:120]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


# Normalize the already-Ruff-formatted lead error to the original patch script's
# expected representation. This is a workspace-only transformation.
replace_once(
    "weatherbot/forecasting/runtime.py",
    '''        if lead_days not in CALIBRATION_LEAD_DAYS:
            raise CalibrationCompatibilityError(
                f"lead_days={lead_days} is outside calibrated lead set {CALIBRATION_LEAD_DAYS}"
            )
        estimate = self.model.probability(
''',
    '''        if lead_days not in CALIBRATION_LEAD_DAYS:
            raise CalibrationCompatibilityError(
                f"lead_days={lead_days} is outside calibrated lead set "
                f"{CALIBRATION_LEAD_DAYS}"
            )
        estimate = self.model.probability(
''',
)

runpy.run_path("scripts/pr53_codex_vintage_patch.py", run_name="__main__")

# A 00:15 retrieval alone does not prove which stitched ECMWF run Open-Meteo served.
# Missing run provenance therefore fails closed; an exact expected 18Z identity is required.
replace_once(
    "weatherbot/forecasting/runtime.py",
    '''        model_run = weather.forecast.model_run_initialized_at_utc
        if model_run is not None and model_run != expected_run:
            raise CalibrationCompatibilityError(
                "forecast model-run initialization differs from the calibrated 18Z vintage"
            )
''',
    '''        model_run = weather.forecast.model_run_initialized_at_utc
        if model_run is None:
            raise CalibrationCompatibilityError(
                "forecast model-run initialization is unavailable; calibrated vintage cannot be proven"
            )
        if model_run != expected_run:
            raise CalibrationCompatibilityError(
                "forecast model-run initialization differs from the calibrated 18Z vintage"
            )
''',
)

# Extend the common quoting fixture without changing existing callers.
replace_once(
    "tests/quoting/helpers.py",
    '''def weather_snapshot(*, issued_at: datetime | None = None) -> WeatherInputSnapshot:
''',
    '''def weather_snapshot(
    *,
    issued_at: datetime | None = None,
    model_run_initialized_at_utc: datetime | None = None,
) -> WeatherInputSnapshot:
''',
)
replace_once(
    "tests/quoting/helpers.py",
    '''        retrieved_at_utc=issued,
    )
''',
    '''        retrieved_at_utc=issued,
        model_run_initialized_at_utc=model_run_initialized_at_utc,
    )
''',
)

runtime_test = Path("tests/forecasting/test_calibration_runtime.py")
source = runtime_test.read_text(encoding="utf-8")
aligned_call = '''weather_snapshot(
        issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC),
        model_run_initialized_at_utc=expected_calibration_model_run(
            target_date=date(2026, 8, 6), lead_days=0
        ),
    )'''
source = source.replace(
    "weather_snapshot(issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC))",
    aligned_call,
)
source += '''


def test_runtime_rejects_missing_model_run_provenance(tmp_path: Path) -> None:
    _approved_repository(tmp_path)
    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)
    weather = weather_snapshot(issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC))
    bucket = TemperatureBucket.bounded(86, 86, TemperatureUnit.FAHRENHEIT)

    with pytest.raises(CalibrationCompatibilityError, match="cannot be proven"):
        runtime.probability(
            city="chicago",
            climate_region="ohio_valley",
            lead_days=0,
            weather=weather,
            bucket=bucket,
        )
'''
runtime_test.write_text(source, encoding="utf-8")

# Make the fail-closed consequence explicit until the production provider can prove run identity.
docs = Path("docs/forecast-calibration.md")
docs_source = docs.read_text(encoding="utf-8")
docs_source += '''

The current stitched production forecast parser does not invent model-run identity. If the provider cannot supply `model_run_initialized_at_utc`, calibrated runtime evaluation rejects the candidate even inside the decision window. This is deliberate: Open-Meteo documents the operational Forecast API as a continuously stitched latest-run series, while Single Runs is the exact-run interface. A later provider change may supply provable run identity, but #48A never guesses it from wall-clock time alone.
'''
docs.write_text(docs_source, encoding="utf-8")
