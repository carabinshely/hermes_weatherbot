from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function_tail(path: str, function_name: str) -> str:
    source = _source(path)
    return source[source.index(f"def {function_name}") :]


def test_public_producer_uses_one_calibrated_probability_boundary() -> None:
    scanner = _source("weatherbot/producer/scanner.py")
    service = _source("weatherbot/producer/service.py")

    assert scanner.count("calibration_runtime.probability(") == 1
    assert "weatherbot.paper" not in scanner
    assert "ExecutionMode" not in scanner
    assert "submit_scanner_candidate(" not in scanner
    assert "evaluate_executable_buy(" in service
    assert "probability=candidate.calibrated.model_probability" in service
    assert "requested_budget=policy.market_reference_notional" in service
    assert "balance=None" in service
    assert "validated = evaluation.quote" in service
    assert "artifact_sha256=candidate.calibrated.artifact_sha256" in service
    assert "place_buy_order(" not in service


def test_internal_paper_consumes_candidate_seam_without_public_sizing_helpers() -> None:
    source = _source("weatherbot/paper/cli.py")
    start = source.index("def scan_once")
    end = source.index("\n\ndef show_status", start)
    scan = source[start:end]

    candidate_collection = scan.index("collect_calibrated_candidates(")
    submission = scan.index("submit_scanner_candidate(")
    assert candidate_collection < submission
    assert "calibrated=candidate.calibrated" in scan
    assert "calc_kelly(" not in scan
    assert "get_adjusted_kelly(" not in scan
    assert "bet_size(" not in scan


def test_public_producer_does_not_reconstruct_edge_from_best_ask() -> None:
    service = _source("weatherbot/producer/service.py")

    assert "book.quote_buy_budget" not in service
    assert "calc_ev(" not in service
    assert "get_adjusted_kelly(" not in service
    assert "bet_size(" not in service
    assert "expected_return=validated.expected_return" in service
    assert "probability_edge=validated.probability_edge" in service


def test_quarantined_historical_live_source_revalidates_before_order_callback() -> None:
    source = _function_tail("bot_v3_legacy_impl.py", "scan_and_trade")
    revalidation = source.index("revalidate_executable_buy(")
    live_gate = source.index('require_live(context, operation="place order")')
    callback = source.index("callback=lambda: place_buy_order(")
    assert revalidation < live_gate < callback
    assert "validated_quote=validated_quote" in source


def test_quarantined_live_order_boundary_does_not_reconstruct_notional_from_price() -> None:
    source = _source("bot_v3_legacy_impl.py")
    start = source.index("def place_buy_order")
    end = source.index("\n\ndef cancel_order", start)
    block = source[start:end]
    assert "validated_quote: ValidatedExecutableQuote" in block
    assert "amount = float(quote.total_cost)" in block
    assert "price_limit = float(quote.worst_price)" in block
    assert "shares * price" not in block
    assert "amount=amount" in block


def test_quote_configuration_declares_every_freshness_and_cost_limit() -> None:
    config = _source("config.json")
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
