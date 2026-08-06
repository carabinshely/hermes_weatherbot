from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def scanner_source() -> str:
    source = (ROOT / "bot_v3.py").read_text(encoding="utf-8")
    start = source.index("def scan_and_trade")
    end = source.index(
        "\n\n# =============================================================================\n# STATUS",
        start,
    )
    return source[start:end]


def test_scanner_uses_one_validated_quote_contract_for_all_modes() -> None:
    source = scanner_source()
    assert "evaluate_executable_buy(" in source
    assert "validated_quote = evaluation.quote" in source
    assert "**validated_quote.metadata()" in source
    assert '"book_cost": book_cost' in source
    assert '"all_in_price": all_in_price' in source
    assert '"cost": cost' in source
    assert "if context.mode is ExecutionMode.RESEARCH:" in source
    assert "if context.mode is ExecutionMode.PAPER:" in source
    assert 'require_live(context, operation="place order")' in source


def test_scanner_does_not_reconstruct_final_edge_from_best_ask() -> None:
    source = scanner_source()
    assert "book.quote_buy_budget" not in source
    assert "preliminary_ev" not in source
    assert "calc_ev(probability, entry_price)" not in source
    assert "execution_slippage > MAX_SLIPPAGE" not in source


def test_live_path_revalidates_before_order_callback() -> None:
    source = scanner_source()
    revalidation = source.index("revalidate_executable_buy(")
    live_gate = source.index('require_live(context, operation="place order")')
    callback = source.index("callback=lambda: place_buy_order(")
    assert revalidation < live_gate < callback
    assert 'price=best_signal["worst_price"]' in source


def test_quote_configuration_declares_every_freshness_and_cost_limit() -> None:
    config = (ROOT / "config.json").read_text(encoding="utf-8")
    for key in (
        "max_forecast_age_seconds",
        "max_event_age_seconds",
        "max_order_book_age_seconds",
        "max_balance_age_seconds",
        "platform_fee_reserve_rate",
        "transaction_cost_reserve",
        "execution_safety_margin_rate",
        "depth_policy",
    ):
        assert f'"{key}"' in config
