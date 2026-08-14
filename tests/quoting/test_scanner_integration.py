from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def public_scanner_source() -> str:
    source = (ROOT / "bot_v3.py").read_text(encoding="utf-8")
    start = source.index("def scan_and_trade")
    end = source.index("\n\ndef show_status", start)
    return source[start:end]


def legacy_scanner_source() -> str:
    source = (ROOT / "bot_v3_legacy.py").read_text(encoding="utf-8")
    start = source.index("def scan_and_trade")
    end = source.index(
        "\n\n# =============================================================================\n# STATUS",
        start,
    )
    return source[start:end]


def test_public_scanner_uses_one_calibrated_probability_boundary() -> None:
    source = public_scanner_source()
    assert "load_calibrated_probability_runtime(" in source
    assert source.count("calibration_runtime.probability(") == 1
    assert "ExecutionMode.PAPER" in source
    assert "submit_scanner_candidate(" in source
    assert "calibrated=calibrated" in source
    assert "evaluate_executable_buy(" in source
    assert "probability=calibrated.model_probability" in source
    assert "validated_quote = evaluation.quote" in source
    assert "**calibrated.audit_metadata()" in source
    assert "**validated_quote.metadata()" in source
    assert '"all_in_price": all_in_price' in source
    assert 'require_live(context, operation="place order")' not in source
    assert "place_buy_order(" not in source


def test_paper_branch_uses_durable_service_before_research_reference_sizing() -> None:
    source = public_scanner_source()
    paper = source.index("if context.mode is ExecutionMode.PAPER:", source.index("calibrated ="))
    submission = source.index("submit_scanner_candidate(", paper)
    research_sizing = source.index("preliminary_kelly =", paper)
    block = source[paper:research_sizing]

    assert paper < submission < research_sizing
    assert "continue" in block
    assert "calc_kelly(" not in block
    assert "get_adjusted_kelly(" not in block
    assert "bet_size(" not in block


def test_research_scanner_does_not_reconstruct_final_edge_from_best_ask() -> None:
    source = public_scanner_source()
    assert "book.quote_buy_budget" not in source
    assert "preliminary_ev" not in source
    assert "calc_ev(probability, entry_price)" not in source
    assert "execution_slippage > MAX_SLIPPAGE" not in source


def test_quarantined_live_path_revalidates_before_order_callback() -> None:
    source = legacy_scanner_source()
    revalidation = source.index("revalidate_executable_buy(")
    live_gate = source.index('require_live(context, operation="place order")')
    callback = source.index("callback=lambda: place_buy_order(")
    assert revalidation < live_gate < callback
    assert "validated_quote=validated_quote" in source


def test_quarantined_live_order_boundary_does_not_reconstruct_notional_from_price() -> None:
    source = (ROOT / "bot_v3_legacy.py").read_text(encoding="utf-8")
    start = source.index("def place_buy_order")
    end = source.index("\n\ndef cancel_order", start)
    block = source[start:end]
    assert "validated_quote: ValidatedExecutableQuote" in block
    assert "amount = float(quote.total_cost)" in block
    assert "price_limit = float(quote.worst_price)" in block
    assert "shares * price" not in block
    assert "amount=amount" in block


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
