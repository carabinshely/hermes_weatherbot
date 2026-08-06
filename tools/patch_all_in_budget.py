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
    executable_budget: Decimal
''',
    '''    requested_budget: Decimal
    book_budget_limit: Decimal
    executable_budget: Decimal
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        requested = as_decimal(self.requested_budget, label="requested budget")
        executable = as_decimal(self.executable_budget, label="executable budget")
        if requested <= 0 or executable <= 0 or executable > requested:
            raise QuoteValidationError("executable budget must be positive and within request")
''',
    '''        requested = as_decimal(self.requested_budget, label="requested budget")
        book_limit = as_decimal(self.book_budget_limit, label="book budget limit")
        executable = as_decimal(self.executable_budget, label="executable budget")
        if requested <= 0:
            raise QuoteValidationError("requested budget must be positive")
        if book_limit <= 0 or book_limit > requested:
            raise QuoteValidationError("book budget limit must be positive and within request")
        if executable <= 0 or executable > book_limit:
            raise QuoteValidationError(
                "executable budget must be positive and within the book budget limit"
            )
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        if normalized["total_all_in_cost"] < self.quote.total_cost:
            raise QuoteValidationError("all-in cost cannot be below order-book cost")
''',
    '''        if executable != self.quote.total_cost:
            raise QuoteValidationError("executable budget must equal order-book cost")
        if normalized["total_all_in_cost"] < self.quote.total_cost:
            raise QuoteValidationError("all-in cost cannot be below order-book cost")
        if normalized["total_all_in_cost"] > requested:
            raise QuoteValidationError("all-in cost exceeds the approved budget")
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        if self.depth_reduced != (executable < requested):
            raise QuoteValidationError("depth reduction flag does not match executable budget")
''',
    '''        if self.depth_reduced != (executable < book_limit):
            raise QuoteValidationError("depth reduction flag does not match the book budget")
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''        object.__setattr__(self, "requested_budget", requested)
        object.__setattr__(self, "executable_budget", executable)
''',
    '''        object.__setattr__(self, "requested_budget", requested)
        object.__setattr__(self, "book_budget_limit", book_limit)
        object.__setattr__(self, "executable_budget", executable)
''',
)
replace_once(
    "weatherbot/quoting/model.py",
    '''            "quote_requested_budget": format(self.requested_budget, "f"),
            "quote_executable_budget": format(self.executable_budget, "f"),
''',
    '''            "quote_requested_budget": format(self.requested_budget, "f"),
            "quote_book_budget_limit": format(self.book_budget_limit, "f"),
            "quote_executable_budget": format(self.executable_budget, "f"),
''',
)

replace_once(
    "weatherbot/quoting/evaluator.py",
    '''    available_notional = sum(
        (level.price * level.size for level in order_book.asks),
        start=Decimal("0"),
    )
    executable_budget = budget
    if available_notional < budget:
        if cost_policy.depth_policy is DepthPolicy.REJECT:
            return _reject(
                evaluated,
                QuoteRejectionReason.INSUFFICIENT_DEPTH,
                (f"requested budget {budget} exceeds displayed ask notional {available_notional}"),
                checks,
            )
        executable_budget = available_notional
''',
    '''    variable_cost_multiplier = (
        Decimal("1")
        + cost_policy.platform_fee_rate
        + cost_policy.safety_margin_rate
    )
    remaining_after_fixed_cost = budget - cost_policy.transaction_cost
    if remaining_after_fixed_cost <= 0:
        return _reject(
            evaluated,
            QuoteRejectionReason.BELOW_MINIMUM_ORDER,
            "approved budget does not cover the fixed transaction-cost reserve",
            checks,
        )
    book_budget_limit = remaining_after_fixed_cost / variable_cost_multiplier
    available_notional = sum(
        (level.price * level.size for level in order_book.asks),
        start=Decimal("0"),
    )
    executable_budget = book_budget_limit
    if available_notional < book_budget_limit:
        if cost_policy.depth_policy is DepthPolicy.REJECT:
            return _reject(
                evaluated,
                QuoteRejectionReason.INSUFFICIENT_DEPTH,
                (
                    f"book budget {book_budget_limit} exceeds displayed ask notional "
                    f"{available_notional}"
                ),
                checks,
            )
        executable_budget = available_notional
''',
)
replace_once(
    "weatherbot/quoting/evaluator.py",
    '''    all_in_average_price = total_all_in_cost / quote.shares
    if all_in_average_price >= cost_policy.maximum_all_in_price:
''',
    '''    if total_all_in_cost > budget:
        return _reject(
            evaluated,
            QuoteRejectionReason.INVALID_INPUT,
            "calculated all-in cost exceeds the approved budget",
            checks,
        )
    all_in_average_price = total_all_in_cost / quote.shares
    if all_in_average_price >= cost_policy.maximum_all_in_price:
''',
)
replace_once(
    "weatherbot/quoting/evaluator.py",
    '''        requested_budget=budget,
        executable_budget=executable_budget,
''',
    '''        requested_budget=budget,
        book_budget_limit=book_budget_limit,
        executable_budget=quote.total_cost,
''',
)
replace_once(
    "weatherbot/quoting/evaluator.py",
    '''        depth_reduced=executable_budget < budget,
''',
    '''        depth_reduced=quote.total_cost < book_budget_limit,
''',
)

replace_once(
    "tests/quoting/test_evaluator.py",
    '''    assert quote.quote.total_cost == Decimal("2")
    assert quote.platform_fee == Decimal("0.02")
    assert quote.transaction_cost == Decimal("0.01")
    assert quote.safety_margin == Decimal("0.04")
    assert quote.total_all_in_cost == Decimal("2.07")
''',
    '''    assert quote.book_budget_limit < quote.requested_budget
    assert quote.quote.total_cost == quote.book_budget_limit
    assert quote.executable_budget == quote.quote.total_cost
    assert quote.platform_fee == quote.quote.total_cost * Decimal("0.01")
    assert quote.transaction_cost == Decimal("0.01")
    assert quote.safety_margin == quote.quote.total_cost * Decimal("0.02")
    assert quote.total_all_in_cost == Decimal("2")
    assert quote.total_all_in_cost <= quote.requested_budget
''',
)
replace_once(
    "tests/quoting/test_evaluator.py",
    '''    assert metadata["quote_total_all_in_cost"] == "2.0700"
''',
    '''    assert Decimal(str(metadata["quote_total_all_in_cost"])) == Decimal("2")
    assert Decimal(str(metadata["quote_book_budget_limit"])) == quote.book_budget_limit
''',
)
replace_once(
    "tests/quoting/test_depth_and_costs.py",
    '''    assert result.detail == "requested budget 6 exceeds displayed ask notional 5.40"
''',
    '''    assert result.detail is not None
    assert "displayed ask notional 5.40" in result.detail
''',
)
replace_once(
    "tests/quoting/test_depth_and_costs.py",
    '''    assert quote.requested_budget == Decimal("6")
    assert quote.executable_budget == Decimal("5.40")
    assert quote.quote.total_cost == Decimal("5.40")
    assert quote.quote.shares == Decimal("13")
    assert quote.depth_reduced
''',
    '''    assert quote.requested_budget == Decimal("6")
    assert quote.book_budget_limit > Decimal("5.40")
    assert quote.executable_budget == Decimal("5.40")
    assert quote.quote.total_cost == Decimal("5.40")
    assert quote.quote.shares == Decimal("13")
    assert quote.total_all_in_cost < quote.requested_budget
    assert quote.depth_reduced
''',
)
