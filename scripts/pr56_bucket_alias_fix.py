from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text(encoding='utf-8')
    if old not in s:
        raise SystemExit(f'anchor not found: {old[:160]!r}')
    p.write_text(s.replace(old, new, 1), encoding='utf-8')

replace_once(
    'weatherbot/paper/integration.py',
    '''    bucket_key = audit_metadata.get("bucket_key")\n    if bucket_key != calibrated.bucket_key:\n        raise ValueError("PAPER bucket_key must match calibrated bucket identity")\n    normalized_keys = {str(key).strip() for key in audit_metadata}\n    collisions = sorted(normalized_keys & _CALIBRATION_AUDIT_KEYS)\n''',
    '''    normalized_keys: dict[str, str] = {}\n    for raw_key in audit_metadata:\n        normalized = str(raw_key).strip()\n        if normalized in normalized_keys:\n            raise ValueError(\n                f"PAPER caller audit metadata contains duplicate normalized key: {normalized}"\n            )\n        normalized_keys[normalized] = str(raw_key)\n    if normalized_keys.get("bucket_key") != "bucket_key":\n        raise ValueError("PAPER caller audit metadata requires one exact bucket_key")\n    bucket_key = audit_metadata.get("bucket_key")\n    if bucket_key != calibrated.bucket_key:\n        raise ValueError("PAPER bucket_key must match calibrated bucket identity")\n    collisions = sorted(set(normalized_keys) & _CALIBRATION_AUDIT_KEYS)\n''',
)

p = Path('tests/paper/test_calibrated_integration.py')
s = p.read_text(encoding='utf-8')
if 'test_scanner_facade_rejects_normalized_duplicate_bucket_key' not in s:
    s += '''\n\ndef test_scanner_facade_rejects_normalized_duplicate_bucket_key(tmp_path: Path) -> None:\n    runtime = PaperRuntimeConfig.from_mapping({"paper_ledger_path": "paper.sqlite3"}, base_dir=tmp_path)\n    calibrated = calibrated_probability()\n    weather = weather_snapshot()\n    event = event_snapshot()\n    decision_book = paper_book(book_hash="duplicate-bucket-key-book")\n\n    def forbidden_fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):\n        raise AssertionError("duplicate normalized key must fail before PAPER book work")\n\n    with pytest.raises(ValueError, match="duplicate normalized key"):\n        submit_scanner_candidate(\n            runtime=runtime,\n            strategy_id="bot-v3-weather",\n            calibrated=calibrated,\n            scope=scope(),\n            weather=weather,\n            event=event,\n            decision_book=decision_book,\n            condition_id=decision_book.condition_id,\n            token_id=decision_book.token_id,\n            evaluated_at=NOW,\n            freshness_policy=freshness_policy(),\n            cost_policy=cost_policy(),\n            fetch_book=forbidden_fetch,\n            audit_metadata={\n                "bucket_key": calibrated.bucket_key,\n                "bucket_key ": "F:90:91",\n            },\n            owner_id="duplicate-bucket-key",\n        )\n\n    assert not runtime.ledger_path.exists()\n'''
p.write_text(s, encoding='utf-8')
