from __future__ import annotations

from pathlib import Path


def test_quarantined_legacy_scanner_uses_resolution_safe_market_contracts() -> None:
    source = Path("bot_v3_legacy.py").read_text(encoding="utf-8")
    required = (
        "parse_gamma_binary_market",
        "parse_temperature_bucket",
        "TemperatureMarketPartition",
        "parse_order_book",
        "BinaryOutcome.YES",
        "MarketCalendar",
        "evaluate_executable_buy",
        "revalidate_executable_buy",
    )
    for symbol in required:
        assert symbol in source


def test_quarantined_legacy_scanner_does_not_use_removed_market_shortcuts() -> None:
    source = Path("bot_v3_legacy.py").read_text(encoding="utf-8")
    assert "(-999," not in source
    assert ", 999)" not in source
    assert 'market["outcomePrices"][0]' not in source
    assert 'market["outcomePrices"][1]' not in source
    assert "datetime.now(timezone.utc).date()" not in source
    assert "get_book(token_id=condition_id)" not in source
    assert "book.quote_buy_budget" not in source


def test_public_research_scanner_delegates_shared_market_parsing_only() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert "_legacy._parse_temperature_markets(event)" in source
    assert "BinaryOutcome.YES" in source
    assert "evaluate_executable_buy(" in source
    assert "revalidate_executable_buy(" not in source
