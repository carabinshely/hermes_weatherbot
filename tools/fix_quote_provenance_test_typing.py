from pathlib import Path

path = Path("tests/quoting/test_revalidation.py")
content = path.read_text(encoding="utf-8")
content = content.replace(
    '''from weatherbot.quoting import (
    QuoteRejectionReason,
''',
    '''from weatherbot.quoting import (
    CostPolicy,
    FreshnessPolicy,
    MarketEventSnapshot,
    QuoteRejectionReason,
''',
    1,
)
movement_marker = "\ndef test_quote_movement_is_re_evaluated_before_execution() -> None:\n"
token_marker = "\ndef test_refreshed_token_mismatch_fails_before_quote_math() -> None:\n"
if content.count(movement_marker) != 1 or content.count(token_marker) != 1:
    raise SystemExit("expected one quote-movement test block")
before_movement, remainder = content.split(movement_marker, 1)
_, after_movement = remainder.split(token_marker, 1)
movement_test = r'''
def test_quote_movement_is_re_evaluated_before_execution() -> None:
    movement_policy = cost_policy(
        maximum_average_slippage="0.03",
        maximum_worst_slippage="0.04",
        maximum_all_in_price="0.90",
        minimum_expected_return="0",
    )
    initial = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=movement_policy,
    )
    validated = initial.quote
    assert validated is not None

    moved = revalidate_executable_buy(
        validated,
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(
            first_ask="0.66",
            second_ask="0.68",
            book_hash="book-hash-moved",
        ),
        balance=balance_snapshot(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=movement_policy,
    )
    assert moved.quote is None
    assert moved.rejection_reason is QuoteRejectionReason.NON_POSITIVE_EDGE
'''
content = (
    before_movement
    + movement_test
    + token_marker
    + after_movement
)
marker = "\ndef test_revalidation_rejects_changed_decision_inputs() -> None:\n"
if content.count(marker) != 1:
    raise SystemExit("expected one revalidation mismatch test marker")
prefix = content.split(marker, 1)[0]
replacement = r'''
def test_revalidation_rejects_changed_decision_inputs() -> None:
    initial = evaluate_executable_buy(
        probability="0.65",
        requested_budget="2",
        weather=weather_snapshot(),
        event=event_snapshot(),
        order_book=order_book(),
        evaluated_at=NOW,
        freshness_policy=freshness_policy(),
        cost_policy=cost_policy(),
    )
    validated = initial.quote
    assert validated is not None

    def assert_mismatch(
        *,
        detail: str,
        probability: str = "0.65",
        requested_budget: str = "2",
        event: MarketEventSnapshot | None = None,
        freshness: FreshnessPolicy | None = None,
        cost: CostPolicy | None = None,
    ) -> None:
        result = revalidate_executable_buy(
            validated,
            probability=probability,
            requested_budget=requested_budget,
            weather=weather_snapshot(),
            event=event or event_snapshot(),
            order_book=order_book(),
            evaluated_at=NOW,
            freshness_policy=freshness or freshness_policy(),
            cost_policy=cost or cost_policy(),
        )
        assert result.rejection_reason is QuoteRejectionReason.SNAPSHOT_MISMATCH
        assert result.detail is not None
        assert detail in result.detail

    assert_mismatch(probability="0.66", detail="probability changed")
    assert_mismatch(requested_budget="3", detail="budget changed")
    original_event = event_snapshot()
    assert_mismatch(
        event=MarketEventSnapshot(
            event_id="other-event",
            retrieved_at_utc=original_event.retrieved_at_utc,
        ),
        detail="other-event",
    )
    assert_mismatch(
        freshness=FreshnessPolicy(
            maximum_forecast_age=timedelta(hours=5),
            maximum_event_age=timedelta(minutes=2),
            maximum_order_book_age=timedelta(seconds=30),
            maximum_balance_age=timedelta(seconds=30),
        ),
        detail="freshness policy changed",
    )
    assert_mismatch(
        cost=cost_policy(fee_rate="0.02"),
        detail="cost policy changed",
    )
'''
path.write_text(prefix + replacement, encoding="utf-8")
