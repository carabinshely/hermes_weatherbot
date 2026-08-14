from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"patch anchor not found in {path}: {old[:180]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/forecasting/runtime.py",
    '''        if self.model_probability <= 0 or self.model_probability >= 1:\n            raise ValueError("model_probability must be between zero and one")\n''',
    '''        if self.model_probability < 0 or self.model_probability > 1:\n            raise ValueError("model_probability must be between zero and one inclusive")\n''',
)
replace_once(
    "weatherbot/forecasting/runtime.py",
    '''        estimate = self.model.probability(\n            city=city,\n            climate_region=climate_region,\n            forecast_source=weather.forecast.source,\n            market_date=weather.forecast.market_date,\n            lead_days=lead_days,\n            forecast_temperature_f=weather.signal_temperature_f,\n            bucket=bucket,\n        )\n        return CalibratedProbability(\n            model_probability=Decimal(str(estimate.probability)),\n''',
    '''        estimate = self.model.probability(\n            city=city,\n            climate_region=climate_region,\n            forecast_source=weather.forecast.source,\n            market_date=weather.forecast.market_date,\n            lead_days=lead_days,\n            forecast_temperature_f=weather.signal_temperature_f,\n            bucket=bucket,\n        )\n        probability = Decimal(str(estimate.probability))\n        if probability <= 0 or probability >= 1:\n            raise CalibrationCompatibilityError(\n                "calibrated endpoint probability is not scanner-eligible"\n            )\n        return CalibratedProbability(\n            model_probability=probability,\n''',
)

replace_once(
    "weatherbot/paper/integration.py",
    '''    collisions = sorted(set(audit_metadata) & _CALIBRATION_AUDIT_KEYS)\n''',
    '''    normalized_keys = {str(key).strip() for key in audit_metadata}\n    collisions = sorted(normalized_keys & _CALIBRATION_AUDIT_KEYS)\n''',
)
replace_once(
    "weatherbot/paper/integration.py",
    '''    strategy_id: str,\n    decision_id: str,\n    calibrated: CalibratedProbability,\n''',
    '''    strategy_id: str,\n    calibrated: CalibratedProbability,\n''',
)
replace_once(
    "weatherbot/paper/integration.py",
    '''    _validate_calibrated_context(calibrated=calibrated, scope=scope, weather=weather)\n    caller_audit = _scanner_audit_metadata(\n''',
    '''    _validate_calibrated_context(calibrated=calibrated, scope=scope, weather=weather)\n    decision_id = paper_scan_decision_id(\n        calibrated=calibrated,\n        scope=scope,\n        weather=weather,\n        event=event,\n        decision_book=decision_book,\n    )\n    caller_audit = _scanner_audit_metadata(\n''',
)

replace_once(
    "bot_v3.py",
    '''                decision_id = paper_scan_decision_id(\n                    calibrated=calibrated,\n                    scope=paper_scope,\n                    weather=weather,\n                    event=event_snapshot,\n                    decision_book=book,\n                )\n                try:\n                    paper_result = submit_scanner_candidate(\n                        runtime=PAPER_RUNTIME,\n                        strategy_id="bot-v3-weather",\n                        decision_id=decision_id,\n                        calibrated=calibrated,\n''',
    '''                try:\n                    paper_result = submit_scanner_candidate(\n                        runtime=PAPER_RUNTIME,\n                        strategy_id="bot-v3-weather",\n                        calibrated=calibrated,\n''',
)
replace_once(
    "bot_v3.py",
    '''        if now_ts - last_resolution >= resolution_interval:\n            try:\n                run_resolution_monitor_cycle()\n            except Exception as exc:\n''',
    '''        if now_ts - last_resolution >= resolution_interval:\n            try:\n                resolution_ledger = (\n                    PAPER_RUNTIME.ledger_path if context.mode is ExecutionMode.PAPER else None\n                )\n                run_resolution_monitor_cycle(resolution_ledger)\n            except Exception as exc:\n''',
)

replace_once(
    "tests/paper/test_calibrated_integration.py",
    '''        runtime=runtime,\n        strategy_id="bot-v3-weather",\n        decision_id=decision_id,\n        calibrated=calibrated,\n''',
    '''        runtime=runtime,\n        strategy_id="bot-v3-weather",\n        calibrated=calibrated,\n''',
)
replace_once(
    "tests/paper/test_calibrated_integration.py",
    '''    assert "probability" not in parameters\n    assert "calibrated" in decision_parameters\n''',
    '''    assert "probability" not in parameters\n    assert "decision_id" not in parameters\n    assert "calibrated" in decision_parameters\n''',
)
replace_once(
    "tests/paper/test_calibrated_integration.py",
    '''    decision_id = paper_scan_decision_id(\n        calibrated=calibrated,\n        scope=scope(),\n        weather=weather,\n        event=event,\n        decision_book=decision_book,\n    )\n\n    def forbidden_fetch''',
    '''    def forbidden_fetch''',
)
replace_once(
    "tests/paper/test_calibrated_integration.py",
    '''            runtime=runtime,\n            strategy_id="bot-v3-weather",\n            decision_id=decision_id,\n            calibrated=calibrated,\n''',
    '''            runtime=runtime,\n            strategy_id="bot-v3-weather",\n            calibrated=calibrated,\n''',
)
replace_once(
    "tests/paper/test_calibrated_integration.py",
    '''                "artifact_sha256": "spoof",\n''',
    '''                "artifact_sha256 ": "spoof",\n''',
)

replace_once(
    "tests/forecasting/test_calibrated_scanner_gate.py",
    '''    events: list[str] = []\n    clock = iter((10000.0,))\n''',
    '''    events: list[str] = []\n    resolved_ledgers: list[object] = []\n    clock = iter((10000.0,))\n''',
)
replace_once(
    "tests/forecasting/test_calibrated_scanner_gate.py",
    '''    def record_resolution(*_args: object, **_kwargs: object) -> None:\n        events.append("resolve")\n''',
    '''    def record_resolution(ledger_path: object = None) -> None:\n        events.append("resolve")\n        resolved_ledgers.append(ledger_path)\n''',
)
replace_once(
    "tests/forecasting/test_calibrated_scanner_gate.py",
    '''    assert events == [f"scan:{mode.value}", "resolve"]\n\n\ndef test_scanner_has_one_shared_calibration_call''',
    '''    assert events == [f"scan:{mode.value}", "resolve"]\n    expected_ledger = bot_v3.PAPER_RUNTIME.ledger_path if mode is ExecutionMode.PAPER else None\n    assert resolved_ledgers == [expected_ledger]\n\n\ndef test_scanner_has_one_shared_calibration_call''',
)

runtime_tests = Path("tests/forecasting/test_calibration_runtime.py")
source = runtime_tests.read_text(encoding="utf-8")
marker = '''\ndef test_runtime_rejects_lead_days_outside_frozen_dataset(tmp_path: Path) -> None:\n'''
if marker not in source:
    raise SystemExit("runtime test insertion marker not found")
endpoint_test = '''\ndef test_runtime_rejects_endpoint_probability_as_candidate_local_compatibility_error(\n    tmp_path: Path,\n) -> None:\n    _approved_repository(tmp_path)\n    runtime = load_calibrated_probability_runtime(repository_root=tmp_path)\n    weather = weather_snapshot(\n        issued_at=datetime(2026, 8, 6, 4, 16, tzinfo=UTC),\n        model_run_initialized_at_utc=expected_calibration_model_run(\n            target_date=date(2026, 8, 6), lead_days=0\n        ),\n    )\n    extreme_bucket = TemperatureBucket.bounded(\n        1000, 1000, TemperatureUnit.FAHRENHEIT\n    )\n\n    with pytest.raises(CalibrationCompatibilityError, match="endpoint probability"):\n        runtime.probability(\n            city="chicago",\n            climate_region="ohio_valley",\n            lead_days=0,\n            weather=weather,\n            bucket=extreme_bucket,\n        )\n\n'''
runtime_tests.write_text(source.replace(marker, endpoint_test + marker, 1), encoding="utf-8")
