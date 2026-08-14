from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text(encoding='utf-8')
    if old not in s:
        raise SystemExit(f'anchor not found in {path}: {old[:160]!r}')
    p.write_text(s.replace(old, new, 1), encoding='utf-8')

replace_once(
    'weatherbot/forecasting/runtime.py',
    'from weatherbot.forecasting.archive import PRODUCTION_FORECAST_CONTRACT_ID\n',
    'from weatherbot.domain import fingerprint\nfrom weatherbot.forecasting.archive import PRODUCTION_FORECAST_CONTRACT_ID\n',
)
replace_once(
    'weatherbot/forecasting/runtime.py',
    '    lead_days: int\n    forecast_source: str\n',
    '    lead_days: int\n    weather_fingerprint: str\n    bucket_key: str\n    forecast_source: str\n',
)
replace_once(
    'weatherbot/forecasting/runtime.py',
    '            ("climate_region", self.climate_region),\n            ("forecast_source", self.forecast_source),\n',
    '            ("climate_region", self.climate_region),\n            ("weather_fingerprint", self.weather_fingerprint),\n            ("bucket_key", self.bucket_key),\n            ("forecast_source", self.forecast_source),\n',
)
replace_once(
    'weatherbot/forecasting/runtime.py',
    '            "lead_days": self.lead_days,\n            "forecast_source": self.forecast_source,\n',
    '            "lead_days": self.lead_days,\n            "weather_fingerprint": self.weather_fingerprint,\n            "bucket_key": self.bucket_key,\n            "forecast_source": self.forecast_source,\n',
)
replace_once(
    'weatherbot/forecasting/runtime.py',
    '            lead_days=lead_days,\n            forecast_source=weather.forecast.source.value,\n',
    '            lead_days=lead_days,\n            weather_fingerprint=fingerprint(weather),\n            bucket_key=bucket.key,\n            forecast_source=weather.forecast.source.value,\n',
)

replace_once(
    'weatherbot/paper/integration.py',
    '        "lead_days",\n        "forecast_source",\n',
    '        "lead_days",\n        "weather_fingerprint",\n        "forecast_source",\n',
)
replace_once(
    'weatherbot/paper/integration.py',
    '    if calibrated.forecast_source != weather.forecast.source.value:\n        raise ValueError("calibrated forecast_source must match PAPER weather source")\n',
    '    if calibrated.forecast_source != weather.forecast.source.value:\n        raise ValueError("calibrated forecast_source must match PAPER weather source")\n    if calibrated.weather_fingerprint != fingerprint(weather):\n        raise ValueError("calibrated weather_fingerprint must match PAPER weather snapshot")\n',
)
replace_once(
    'weatherbot/paper/integration.py',
    '    normalized_keys = {str(key).strip() for key in audit_metadata}\n',
    '    bucket_key = audit_metadata.get("bucket_key")\n    if bucket_key != calibrated.bucket_key:\n        raise ValueError("PAPER bucket_key must match calibrated bucket identity")\n    normalized_keys = {str(key).strip() for key in audit_metadata}\n',
)

replace_once(
    'tests/paper/helpers.py',
    'from weatherbot.domain import MarketId, OutcomeId, PositionKey, RiskScope\n',
    'from weatherbot.domain import MarketId, OutcomeId, PositionKey, RiskScope, fingerprint\n',
)
replace_once(
    'tests/paper/helpers.py',
    '        lead_days=lead_days,\n        forecast_source="open_meteo_ecmwf_ifs025",\n',
    '        lead_days=lead_days,\n        weather_fingerprint=fingerprint(weather_snapshot()),\n        bucket_key="F:85:86",\n        forecast_source="open_meteo_ecmwf_ifs025",\n',
)

p = Path('tests/paper/test_calibrated_integration.py')
s = p.read_text(encoding='utf-8')
insert = '''\n\ndef test_scanner_facade_rejects_probability_from_different_weather_snapshot() -> None:\n    calibrated = calibrated_probability()\n    refreshed_weather = weather_snapshot(issued_at=NOW - __import__('datetime').timedelta(minutes=30))\n    event = event_snapshot()\n    decision_book = paper_book(book_hash="refreshed-weather-book")\n\n    with pytest.raises(ValueError, match="weather_fingerprint"):\n        paper_scan_decision_id(\n            calibrated=calibrated,\n            scope=scope(),\n            weather=refreshed_weather,\n            event=event,\n            decision_book=decision_book,\n        )\n\n\ndef test_scanner_facade_rejects_bucket_mismatch_before_ledger_mutation(tmp_path: Path) -> None:\n    runtime = PaperRuntimeConfig.from_mapping({"paper_ledger_path": "paper.sqlite3"}, base_dir=tmp_path)\n    calibrated = calibrated_probability()\n    weather = weather_snapshot()\n    event = event_snapshot()\n    decision_book = paper_book(book_hash="bucket-mismatch-book")\n\n    def forbidden_fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):\n        raise AssertionError("bucket mismatch must fail before PAPER book work")\n\n    with pytest.raises(ValueError, match="bucket_key"):\n        submit_scanner_candidate(\n            runtime=runtime,\n            strategy_id="bot-v3-weather",\n            calibrated=calibrated,\n            scope=scope(),\n            weather=weather,\n            event=event,\n            decision_book=decision_book,\n            condition_id=decision_book.condition_id,\n            token_id=decision_book.token_id,\n            evaluated_at=NOW,\n            freshness_policy=freshness_policy(),\n            cost_policy=cost_policy(),\n            fetch_book=forbidden_fetch,\n            audit_metadata={"bucket_key": "F:90:91"},\n            owner_id="bucket-mismatch",\n        )\n\n    assert not runtime.ledger_path.exists()\n'''
if 'test_scanner_facade_rejects_probability_from_different_weather_snapshot' not in s:
    p.write_text(s + insert, encoding='utf-8')

p = Path('tests/forecasting/test_calibration_runtime.py')
s = p.read_text(encoding='utf-8')
old = '''        "lead_days": 0,\n        "forecast_source": result.forecast_source,\n'''
new = '''        "lead_days": 0,\n        "weather_fingerprint": result.weather_fingerprint,\n        "bucket_key": result.bucket_key,\n        "forecast_source": result.forecast_source,\n'''
if old not in s:
    raise SystemExit('calibration audit expectation anchor missing')
p.write_text(s.replace(old, new, 1), encoding='utf-8')
