from __future__ import annotations

from pathlib import Path


def test_quarantined_legacy_implementation_retains_resolution_safe_market_contracts() -> None:
    source = Path("bot_v3_legacy_impl.py").read_text(encoding="utf-8")
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


def test_public_producer_uses_shared_market_contracts_without_legacy_imports() -> None:
    scanner = Path("weatherbot/producer/scanner.py").read_text(encoding="utf-8")
    market_http = Path("weatherbot/markets/public_http.py").read_text(encoding="utf-8")
    service = Path("weatherbot/producer/service.py").read_text(encoding="utf-8")

    assert "parse_temperature_markets(event)" in scanner
    assert "BinaryOutcome.YES" in scanner
    assert "parse_gamma_binary_market" in market_http
    assert "parse_order_book" in market_http
    assert "evaluate_executable_buy(" in service
    assert "revalidate_executable_buy(" not in service
    assert "bot_v3_legacy" not in scanner
    assert "bot_v3_legacy" not in service
