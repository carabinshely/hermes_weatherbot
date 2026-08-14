from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"anchor not found in {path}: {old[:160]!r}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/paper/integration.py",
    "from datetime import datetime\n",
    "from datetime import UTC, datetime\n",
)
replace_once(
    "weatherbot/paper/integration.py",
    "_CALIBRATION_AUDIT_KEYS = frozenset(\n",
    "def _utc_now() -> datetime:\n    return datetime.now(UTC)\n\n\n_CALIBRATION_AUDIT_KEYS = frozenset(\n",
)
replace_once(
    "weatherbot/paper/integration.py",
    "    token_id: OutcomeTokenId,\n    evaluated_at: datetime,\n    freshness_policy: FreshnessPolicy,\n",
    "    token_id: OutcomeTokenId,\n    freshness_policy: FreshnessPolicy,\n",
)
replace_once(
    "weatherbot/paper/integration.py",
    "        valuation_books = load_open_position_books(store, fetch_book)\n        execution_book = fetch_book(condition_id, token_id)\n        request = PaperEntryRequest(\n",
    "        valuation_books = load_open_position_books(store, fetch_book)\n        execution_book = fetch_book(condition_id, token_id)\n        evaluated_at = _utc_now()\n        request = PaperEntryRequest(\n",
)

replace_once(
    "bot_v3.py",
    "            if context.mode is ExecutionMode.PAPER:\n                evaluated_at = datetime.now(UTC)\n                paper_scope = RiskScope(\n",
    "            if context.mode is ExecutionMode.PAPER:\n                paper_scope = RiskScope(\n",
)
replace_once(
    "bot_v3.py",
    "                        token_id=selection.token_id,\n                        evaluated_at=evaluated_at,\n                        freshness_policy=_legacy._quote_freshness_policy(),\n",
    "                        token_id=selection.token_id,\n                        freshness_policy=_legacy._quote_freshness_policy(),\n",
)

p = Path("tests/paper/test_calibrated_integration.py")
s = p.read_text(encoding="utf-8")
s = s.replace(
    "import pytest\n\nfrom tests.paper.helpers",
    "import pytest\n\nimport weatherbot.paper.integration as paper_integration\nfrom tests.paper.helpers",
    1,
)
s = s.replace("        evaluated_at=NOW,\n", "")
s = s.replace(
    '    assert "decision_id" not in parameters\n',
    '    assert "decision_id" not in parameters\n    assert "evaluated_at" not in parameters\n',
    1,
)
s = s.replace(
    "def test_calibrated_paper_entry_persists_complete_probability_provenance(tmp_path: Path) -> None:\n    runtime, calibrated, decision_id, result = _submit(tmp_path)\n",
    "def test_calibrated_paper_entry_persists_complete_probability_provenance(\n    tmp_path: Path, monkeypatch: pytest.MonkeyPatch\n) -> None:\n    monkeypatch.setattr(paper_integration, \"_utc_now\", lambda: NOW)\n    runtime, calibrated, decision_id, result = _submit(tmp_path)\n",
    1,
)
if "test_scanner_facade_timestamps_after_fresh_execution_book" not in s:
    s += '''\n\ndef test_scanner_facade_timestamps_after_fresh_execution_book(\n    tmp_path: Path, monkeypatch: pytest.MonkeyPatch\n) -> None:\n    runtime = PaperRuntimeConfig.from_mapping(\n        {"paper_ledger_path": "paper.sqlite3"}, base_dir=tmp_path\n    )\n    calibrated = calibrated_probability()\n    weather = weather_snapshot()\n    event = event_snapshot()\n    decision_book = paper_book(book_hash="timing-decision-book")\n    events: list[str] = []\n\n    def fetch(_condition_id: ConditionId, _token_id: OutcomeTokenId):\n        events.append("fetch")\n        return paper_book(book_hash="timing-execution-book")\n\n    def now_after_fetch():\n        assert events == ["fetch"]\n        events.append("clock")\n        return NOW\n\n    monkeypatch.setattr(paper_integration, "_utc_now", now_after_fetch)\n\n    result = submit_scanner_candidate(\n        runtime=runtime,\n        strategy_id="bot-v3-weather",\n        calibrated=calibrated,\n        scope=scope(),\n        weather=weather,\n        event=event,\n        decision_book=decision_book,\n        condition_id=decision_book.condition_id,\n        token_id=decision_book.token_id,\n        freshness_policy=freshness_policy(),\n        cost_policy=cost_policy(),\n        fetch_book=fetch,\n        audit_metadata={"bucket_key": calibrated.bucket_key},\n        owner_id="timing-test",\n    )\n\n    assert result.status is PaperEntryStatus.FILLED\n    assert events == ["fetch", "clock"]\n'''
p.write_text(s, encoding="utf-8")
