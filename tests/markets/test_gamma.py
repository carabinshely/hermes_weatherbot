from __future__ import annotations

from decimal import Decimal

import pytest

from weatherbot.markets import (
    BinaryOutcome,
    ConditionId,
    GammaMarketError,
    MarketIdentityError,
    OutcomeTokenId,
    parse_gamma_binary_market,
)

CONDITION = "0x" + "ab" * 32
YES_TOKEN = "123456789012345678901234567890"
NO_TOKEN = "987654321098765432109876543210"


def payload(
    *,
    outcomes: object = '["Yes", "No"]',
    tokens: object = f'["{YES_TOKEN}", "{NO_TOKEN}"]',
    prices: object = '["0.34", "0.66"]',
) -> dict[str, object]:
    return {
        "id": "1996416",
        "question": "Will the highest temperature in Chicago be between 62-63°F on April 18?",
        "conditionId": CONDITION,
        "outcomes": outcomes,
        "clobTokenIds": tokens,
        "outcomePrices": prices,
        "description": "Resolution rules",
        "resolutionSource": "https://example.test/source",
        "endDate": "2026-04-19T04:00:00Z",
        "active": True,
        "closed": False,
    }


def test_maps_yes_and_no_tokens_by_label_not_position() -> None:
    market = parse_gamma_binary_market(payload())
    assert str(market.select(BinaryOutcome.YES).token_id) == YES_TOKEN
    assert str(market.select(BinaryOutcome.NO).token_id) == NO_TOKEN
    assert market.descriptive_price(BinaryOutcome.YES) == Decimal("0.34")
    assert market.descriptive_price(BinaryOutcome.NO) == Decimal("0.66")


def test_reversed_outcome_order_preserves_explicit_mapping() -> None:
    market = parse_gamma_binary_market(
        payload(
            outcomes=["No", "Yes"],
            tokens=[NO_TOKEN, YES_TOKEN],
            prices=["0.66", "0.34"],
        )
    )
    yes = market.select(BinaryOutcome.YES)
    no = market.select(BinaryOutcome.NO)
    assert str(yes.token_id) == YES_TOKEN
    assert str(no.token_id) == NO_TOKEN
    assert yes.log_label.endswith(f"outcome=YES token={YES_TOKEN}")


@pytest.mark.parametrize(
    ("outcomes", "tokens"),
    [
        (["Yes"], [YES_TOKEN]),
        (["Yes", "Yes"], [YES_TOKEN, NO_TOKEN]),
        (["Maybe", "No"], [YES_TOKEN, NO_TOKEN]),
        (["Yes", "No"], [YES_TOKEN, YES_TOKEN]),
        (["Yes", "No"], [YES_TOKEN]),
    ],
)
def test_rejects_missing_duplicated_or_ambiguous_token_mapping(
    outcomes: object,
    tokens: object,
) -> None:
    with pytest.raises(GammaMarketError):
        parse_gamma_binary_market(payload(outcomes=outcomes, tokens=tokens))


def test_identifier_classes_cannot_be_substituted() -> None:
    condition = ConditionId(CONDITION)
    token = OutcomeTokenId(YES_TOKEN)
    assert condition != token
    with pytest.raises(MarketIdentityError):
        OutcomeTokenId(CONDITION)
    with pytest.raises(MarketIdentityError):
        ConditionId(YES_TOKEN)


def test_outcome_prices_are_descriptive_not_a_bid_ask_pair() -> None:
    market = parse_gamma_binary_market(payload(prices=["0.34", "0.66"]))
    assert market.descriptive_price(BinaryOutcome.YES) == Decimal("0.34")
    assert market.descriptive_price(BinaryOutcome.NO) == Decimal("0.66")
    assert not hasattr(market, "spread")
    assert not hasattr(market, "best_ask")
