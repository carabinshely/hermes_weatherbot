from pathlib import Path

model = Path("weatherbot/quoting/model.py")
content = model.read_text(encoding="utf-8")
content = content.replace("from typing import TypeAlias\n", "")
content = content.replace(
    "QuoteMetadataValue: TypeAlias = str | float | bool | None",
    "type QuoteMetadataValue = str | float | bool | None",
)
model.write_text(content, encoding="utf-8")

helpers = Path("tests/quoting/helpers.py")
content = helpers.read_text(encoding="utf-8")
content = content.replace(
    "from weatherbot.markets import ConditionId, OutcomeTokenId, parse_order_book\n",
    "from weatherbot.markets import (\n"
    "    ConditionId,\n"
    "    OrderBookSnapshot,\n"
    "    OutcomeTokenId,\n"
    "    parse_order_book,\n"
    ")\n",
)
content = content.replace(
    '''def order_book(**kwargs: object):
    return parse_order_book(
        order_book_payload(**kwargs),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
''',
    '''def order_book(
    *,
    observed_at: datetime | None = None,
    first_ask: str = "0.40",
    second_ask: str = "0.42",
    first_size: str = "3",
    second_size: str = "10",
    book_hash: str = "book-hash-1",
) -> OrderBookSnapshot:
    return parse_order_book(
        order_book_payload(
            observed_at=observed_at,
            first_ask=first_ask,
            second_ask=second_ask,
            first_size=first_size,
            second_size=second_size,
            book_hash=book_hash,
        ),
        expected_condition_id=CONDITION,
        expected_token_id=TOKEN,
    )
''',
)
helpers.write_text(content, encoding="utf-8")
