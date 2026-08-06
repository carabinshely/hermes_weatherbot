from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/markets/orderbook.py",
    '''        return ExecutableQuote(
            token_id=self.token_id,
            shares=requested,
            total_cost=cost,
            average_price=cost / requested,
            worst_price=worst_price,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            observed_at=self.observed_at,
            book_hash=self.book_hash,
        )


def _levels(value: object, *, side: str) -> tuple[OrderLevel, ...]:
''',
    '''        return ExecutableQuote(
            token_id=self.token_id,
            shares=requested,
            total_cost=cost,
            average_price=cost / requested,
            worst_price=worst_price,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            observed_at=self.observed_at,
            book_hash=self.book_hash,
        )

    def quote_buy_budget(
        self,
        budget: Decimal | int | str | float,
    ) -> ExecutableQuote:
        """Consume ask depth without spending more than the approved cash budget."""
        requested_budget = _decimal(budget, label="cash budget")
        if requested_budget <= 0:
            raise OrderBookError("cash budget must be positive")

        remaining_budget = requested_budget
        shares = Decimal("0")
        cost = Decimal("0")
        worst_price: Decimal | None = None

        for level in self.asks:
            level_cost = level.size * level.price
            if remaining_budget >= level_cost:
                shares += level.size
                cost += level_cost
                remaining_budget -= level_cost
                worst_price = level.price
            else:
                shares += remaining_budget / level.price
                cost += remaining_budget
                remaining_budget = Decimal("0")
                worst_price = level.price
            if remaining_budget == 0:
                break

        if remaining_budget > 0:
            available_budget = requested_budget - remaining_budget
            raise OrderBookError(
                "insufficient ask depth for cash budget: "
                f"requested {requested_budget}, executable {available_budget}"
            )
        if shares < self.minimum_order_size:
            raise OrderBookError(
                f"cash budget buys {shares} shares, below minimum {self.minimum_order_size}"
            )
        assert worst_price is not None
        if cost > requested_budget:
            raise OrderBookError("executable quote exceeds approved cash budget")

        return ExecutableQuote(
            token_id=self.token_id,
            shares=shares,
            total_cost=cost,
            average_price=cost / shares,
            worst_price=worst_price,
            best_bid=self.best_bid,
            best_ask=self.best_ask,
            observed_at=self.observed_at,
            book_hash=self.book_hash,
        )


def _levels(value: object, *, side: str) -> tuple[OrderLevel, ...]:
''',
)

replace_once(
    "bot_v3.py",
    "from decimal import Decimal, ROUND_DOWN\n",
    "from decimal import Decimal\n",
)
replace_once(
    "bot_v3.py",
    '''            shares_decimal = (Decimal(str(size)) / book.best_ask).quantize(
                Decimal("0.000001"), rounding=ROUND_DOWN
            )
            try:
                quote = book.quote_buy(shares_decimal)
''',
    '''            try:
                quote = book.quote_buy_budget(Decimal(str(size)))
''',
)

replace_once(
    "tests/markets/test_orderbook.py",
    '''def test_size_aware_average_fill_uses_available_ask_depth() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    quote = book.quote_buy(Decimal("5"))
    assert quote.total_cost == Decimal("2.04")
    assert quote.average_price == Decimal("0.408")
    assert quote.worst_price == Decimal("0.42")
    assert quote.best_ask == Decimal("0.40")


''',
    '''def test_size_aware_average_fill_uses_available_ask_depth() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    quote = book.quote_buy(Decimal("5"))
    assert quote.total_cost == Decimal("2.04")
    assert quote.average_price == Decimal("0.408")
    assert quote.worst_price == Decimal("0.42")
    assert quote.best_ask == Decimal("0.40")


def test_cash_budget_quote_absorbs_depth_without_overspending() -> None:
    book = parse_order_book(
        payload(),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
    budget = Decimal("2")
    quote = book.quote_buy_budget(budget)
    assert quote.total_cost == budget
    assert quote.total_cost <= budget
    assert quote.shares == Decimal("3") + Decimal("0.80") / Decimal("0.42")
    assert quote.average_price == quote.total_cost / quote.shares
    assert quote.worst_price == Decimal("0.42")
    assert quote.best_ask == Decimal("0.40")


''',
)
replace_once(
    "tests/markets/test_orderbook.py",
    '''    with pytest.raises(OrderBookError, match="insufficient"):
        book.quote_buy(Decimal("20"))
''',
    '''    with pytest.raises(OrderBookError, match="insufficient"):
        book.quote_buy(Decimal("20"))
    with pytest.raises(OrderBookError, match="below minimum"):
        book.quote_buy_budget(Decimal("0.30"))
    with pytest.raises(OrderBookError, match="insufficient"):
        book.quote_buy_budget(Decimal("10"))
''',
)
replace_once(
    "tests/markets/test_bot_integration_source.py",
    '        "quote_buy",\n',
    '        "quote_buy_budget",\n',
)
