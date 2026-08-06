from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/quoting/model.py",
    '''    requested_budget: Decimal
    book_budget_limit: Decimal
    executable_budget: Decimal
    platform_fee: Decimal
''',
    '''    requested_budget: Decimal
    book_budget_limit: Decimal
    executable_budget: Decimal
    freshness_policy: FreshnessPolicy
    cost_policy: CostPolicy
    platform_fee: Decimal
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        if normalized["total_all_in_cost"] < self.quote.total_cost:
            raise QuoteValidationError("all-in cost cannot be below order-book cost")
        if normalized["total_all_in_cost"] > requested:
            raise QuoteValidationError("all-in cost exceeds the approved budget")
''',
    '''        if normalized["platform_fee"] != (
            self.quote.total_cost * self.cost_policy.platform_fee_rate
        ):
            raise QuoteValidationError("platform fee does not match the recorded policy")
        if normalized["transaction_cost"] != self.cost_policy.transaction_cost:
            raise QuoteValidationError(
                "transaction cost does not match the recorded policy"
            )
        if normalized["safety_margin"] != (
            self.quote.total_cost * self.cost_policy.safety_margin_rate
        ):
            raise QuoteValidationError("safety margin does not match the recorded policy")
        expected_average_slippage = self.quote.average_price - self.quote.best_ask
        expected_worst_slippage = self.quote.worst_price - self.quote.best_ask
        if normalized["average_slippage"] != expected_average_slippage:
            raise QuoteValidationError("average slippage does not reconcile")
        if normalized["worst_slippage"] != expected_worst_slippage:
            raise QuoteValidationError("worst slippage does not reconcile")
        if normalized["average_slippage"] > self.cost_policy.maximum_average_slippage:
            raise QuoteValidationError("average slippage exceeds the recorded policy")
        if normalized["worst_slippage"] > self.cost_policy.maximum_worst_slippage:
            raise QuoteValidationError("worst slippage exceeds the recorded policy")
        if normalized["total_all_in_cost"] < self.quote.total_cost:
            raise QuoteValidationError("all-in cost cannot be below order-book cost")
        if normalized["total_all_in_cost"] > requested:
            raise QuoteValidationError("all-in cost exceeds the approved budget")
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        if expected_return != expected_profit / normalized["total_all_in_cost"]:
            raise QuoteValidationError("expected return does not reconcile")
        if edge != probability - normalized["all_in_average_price"]:
            raise QuoteValidationError("probability edge does not reconcile")
        if self.depth_reduced != (executable < book_limit):
            raise QuoteValidationError("depth reduction flag does not match the book budget")
        freshness = MappingProxyType(dict(self.freshness))
''',
    '''        if expected_return != expected_profit / normalized["total_all_in_cost"]:
            raise QuoteValidationError("expected return does not reconcile")
        if edge != probability - normalized["all_in_average_price"]:
            raise QuoteValidationError("probability edge does not reconcile")
        if normalized["all_in_average_price"] >= self.cost_policy.maximum_all_in_price:
            raise QuoteValidationError("all-in price exceeds the recorded policy")
        if expected_profit <= 0 or edge <= 0:
            raise QuoteValidationError("validated quote must retain positive executable edge")
        if expected_return < self.cost_policy.minimum_expected_return:
            raise QuoteValidationError("expected return is below the recorded policy")
        if self.depth_reduced != (executable < book_limit):
            raise QuoteValidationError("depth reduction flag does not match the book budget")
        if self.depth_reduced and self.cost_policy.depth_policy is DepthPolicy.REJECT:
            raise QuoteValidationError("reject-depth policy cannot produce a reduced quote")
        freshness = MappingProxyType(dict(self.freshness))
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        if any(not check.fresh for check in freshness.values()):
            raise QuoteValidationError("validated quote contains a stale freshness check")
        object.__setattr__(self, "model_probability", probability)
''',
    '''        if any(not check.fresh for check in freshness.values()):
            raise QuoteValidationError("validated quote contains a stale freshness check")
        expected_maximum_ages = {
            "forecast": self.freshness_policy.maximum_forecast_age.total_seconds(),
            "event": self.freshness_policy.maximum_event_age.total_seconds(),
            "order_book": self.freshness_policy.maximum_order_book_age.total_seconds(),
            "balance": self.freshness_policy.maximum_balance_age.total_seconds(),
        }
        for label, check in freshness.items():
            expected_maximum = expected_maximum_ages.get(label)
            if expected_maximum is None:
                raise QuoteValidationError(f"unsupported freshness label: {label}")
            if check.maximum_age_seconds != expected_maximum:
                raise QuoteValidationError(
                    f"{label} freshness limit differs from the recorded policy"
                )
        object.__setattr__(self, "model_probability", probability)
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''            "probability": format(self.model_probability, "f"),
            "evaluated_at": self.evaluated_at_utc.isoformat(),
''',
    '''            "probability": format(self.model_probability, "f"),
            "platform_fee_rate": format(self.cost_policy.platform_fee_rate, "f"),
            "transaction_cost": format(self.cost_policy.transaction_cost, "f"),
            "safety_margin_rate": format(self.cost_policy.safety_margin_rate, "f"),
            "maximum_average_slippage": format(
                self.cost_policy.maximum_average_slippage, "f"
            ),
            "maximum_worst_slippage": format(
                self.cost_policy.maximum_worst_slippage, "f"
            ),
            "maximum_all_in_price": format(
                self.cost_policy.maximum_all_in_price, "f"
            ),
            "minimum_expected_return": format(
                self.cost_policy.minimum_expected_return, "f"
            ),
            "depth_policy": self.cost_policy.depth_policy.value,
            "evaluated_at": self.evaluated_at_utc.isoformat(),
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''            "quote_platform_fee": format(self.platform_fee, "f"),
            "quote_transaction_cost": format(self.transaction_cost, "f"),
            "quote_safety_margin": format(self.safety_margin, "f"),
''',
    '''            "quote_platform_fee": format(self.platform_fee, "f"),
            "quote_platform_fee_rate": format(
                self.cost_policy.platform_fee_rate, "f"
            ),
            "quote_transaction_cost": format(self.transaction_cost, "f"),
            "quote_safety_margin": format(self.safety_margin, "f"),
            "quote_safety_margin_rate": format(
                self.cost_policy.safety_margin_rate, "f"
            ),
            "quote_maximum_average_slippage": format(
                self.cost_policy.maximum_average_slippage, "f"
            ),
            "quote_maximum_worst_slippage": format(
                self.cost_policy.maximum_worst_slippage, "f"
            ),
            "quote_maximum_all_in_price": format(
                self.cost_policy.maximum_all_in_price, "f"
            ),
            "quote_minimum_expected_return": format(
                self.cost_policy.minimum_expected_return, "f"
            ),
            "quote_depth_policy": self.cost_policy.depth_policy.value,
            "quote_future_tolerance_seconds": (
                self.freshness_policy.future_tolerance.total_seconds()
            ),
''',
)

replace_once(
    "weatherbot/quoting/evaluator.py",
    '''        requested_budget=budget,
        book_budget_limit=book_budget_limit,
        executable_budget=quote.total_cost,
        platform_fee=platform_fee,
''',
    '''        requested_budget=budget,
        book_budget_limit=book_budget_limit,
        executable_budget=quote.total_cost,
        freshness_policy=freshness_policy,
        cost_policy=cost_policy,
        platform_fee=platform_fee,
''',
)
replace_once(
    "weatherbot/quoting/evaluator.py",
    '''    if order_book.token_id != previous.token_id:
        return _reject(
            as_utc(evaluated_at, label="quote evaluation time"),
            QuoteRejectionReason.SNAPSHOT_MISMATCH,
            (
                f"refreshed token {order_book.token_id} does not match "
                f"validated token {previous.token_id}"
            ),
        )
    return evaluate_executable_buy(
''',
    '''    evaluated = as_utc(evaluated_at, label="quote evaluation time")
    try:
        current_probability = as_decimal(probability, label="model probability")
        current_budget = as_decimal(requested_budget, label="requested budget")
    except QuoteValidationError as exc:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            str(exc),
        )
    mismatch: str | None = None
    if order_book.token_id != previous.token_id:
        mismatch = (
            f"refreshed token {order_book.token_id} does not match "
            f"validated token {previous.token_id}"
        )
    elif event.event_id != previous.event_id:
        mismatch = (
            f"refreshed event {event.event_id} does not match "
            f"validated event {previous.event_id}"
        )
    elif current_probability != previous.model_probability:
        mismatch = "model probability changed after quote validation"
    elif current_budget != previous.requested_budget:
        mismatch = "requested budget changed after quote validation"
    elif freshness_policy != previous.freshness_policy:
        mismatch = "freshness policy changed after quote validation"
    elif cost_policy != previous.cost_policy:
        mismatch = "cost policy changed after quote validation"
    if mismatch is not None:
        return _reject(
            evaluated,
            QuoteRejectionReason.SNAPSHOT_MISMATCH,
            mismatch,
        )
    return evaluate_executable_buy(
''',
)

replace_once(
    "tests/quoting/test_evaluator.py",
    '''        cost_policy=cost_policy(),
    )
    assert result.accepted
''',
    '''        cost_policy=cost_policy(),
    )
    assert result.accepted
''',
)
replace_once(
    "tests/quoting/test_evaluator.py",
    '''    assert quote.freshness["balance"].age_seconds == 2

    metadata = quote.metadata()
''',
    '''    assert quote.freshness["balance"].age_seconds == 2
    assert quote.cost_policy.platform_fee_rate == Decimal("0.01")
    assert quote.cost_policy.depth_policy is DepthPolicy.REJECT
    assert quote.freshness_policy.maximum_order_book_age == timedelta(seconds=30)

    metadata = quote.metadata()
''',
)
replace_once(
    "tests/quoting/test_evaluator.py",
    '''from weatherbot.quoting import (
    QuoteRejectionReason,
    evaluate_executable_buy,
)
''',
    '''from weatherbot.quoting import (
    DepthPolicy,
    QuoteRejectionReason,
    evaluate_executable_buy,
)
''',
)
replace_once(
    "tests/quoting/test_evaluator.py",
    '''    assert metadata["balance_freshness_passed"] is True
    assert isinstance(metadata["quote_fingerprint"], str)
''',
    '''    assert metadata["balance_freshness_passed"] is True
    assert metadata["quote_platform_fee_rate"] == "0.01"
    assert metadata["quote_safety_margin_rate"] == "0.02"
    assert metadata["quote_depth_policy"] == "reject"
    assert metadata["quote_future_tolerance_seconds"] == 5.0
    assert isinstance(metadata["quote_fingerprint"], str)
''',
)

path = Path("tests/quoting/test_revalidation.py")
content = path.read_text(encoding="utf-8")
content += '''

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
    assert initial.quote is not None

    changed_event = type(event_snapshot())(
        event_id="other-event",
        retrieved_at_utc=event_snapshot().retrieved_at_utc,
    )
    cases = (
        {
            "probability": "0.66",
            "requested_budget": "2",
            "event": event_snapshot(),
            "freshness": freshness_policy(),
            "cost": cost_policy(),
            "detail": "probability changed",
        },
        {
            "probability": "0.65",
            "requested_budget": "3",
            "event": event_snapshot(),
            "freshness": freshness_policy(),
            "cost": cost_policy(),
            "detail": "budget changed",
        },
        {
            "probability": "0.65",
            "requested_budget": "2",
            "event": changed_event,
            "freshness": freshness_policy(),
            "cost": cost_policy(),
            "detail": "other-event",
        },
        {
            "probability": "0.65",
            "requested_budget": "2",
            "event": event_snapshot(),
            "freshness": type(freshness_policy())(
                maximum_forecast_age=timedelta(hours=5),
                maximum_event_age=timedelta(minutes=2),
                maximum_order_book_age=timedelta(seconds=30),
                maximum_balance_age=timedelta(seconds=30),
            ),
            "cost": cost_policy(),
            "detail": "freshness policy changed",
        },
        {
            "probability": "0.65",
            "requested_budget": "2",
            "event": event_snapshot(),
            "freshness": freshness_policy(),
            "cost": cost_policy(fee_rate="0.02"),
            "detail": "cost policy changed",
        },
    )
    for case in cases:
        result = revalidate_executable_buy(
            initial.quote,
            probability=case["probability"],
            requested_budget=case["requested_budget"],
            weather=weather_snapshot(),
            event=case["event"],
            order_book=order_book(),
            evaluated_at=NOW,
            freshness_policy=case["freshness"],
            cost_policy=case["cost"],
        )
        assert result.rejection_reason is QuoteRejectionReason.SNAPSHOT_MISMATCH
        assert result.detail is not None
        assert case["detail"] in result.detail
'''
content = content.replace(
    "from tests.quoting.helpers import (\n",
    "from datetime import timedelta\n\nfrom tests.quoting.helpers import (\n",
)
path.write_text(content, encoding="utf-8")
