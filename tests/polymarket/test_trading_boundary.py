from __future__ import annotations

import pytest

from weatherbot.polymarket import (
    AccountConfiguration,
    AccountSignatureType,
    AuthenticatedPolymarketTrading,
    AuthenticatedTradingUnavailable,
    UnsupportedAccountConfiguration,
)
from weatherbot.polymarket.legacy import MarketOrderArgs, UnsupportedTradingClient


def test_supported_account_signature_configurations() -> None:
    eoa = AccountConfiguration.from_values(signature_type=0)
    proxy = AccountConfiguration.from_values(
        signature_type=1,
        funder_address="0xproxy",
    )
    safe = AccountConfiguration.from_values(
        signature_type=2,
        funder_address="0xsafe",
    )
    poly_1271 = AccountConfiguration.from_values(
        signature_type=3,
        funder_address="0xembedded",
    )

    assert eoa.signature_type is AccountSignatureType.EOA
    assert proxy.signature_type is AccountSignatureType.POLY_PROXY
    assert safe.signature_type is AccountSignatureType.POLY_GNOSIS_SAFE
    assert poly_1271.signature_type is AccountSignatureType.POLY_1271


def test_unsupported_signature_type_fails_before_client_construction() -> None:
    with pytest.raises(UnsupportedAccountConfiguration, match="unsupported"):
        AccountConfiguration.from_values(signature_type=99)


def test_proxy_modes_require_funder_address() -> None:
    for signature_type in (1, 2, 3):
        with pytest.raises(UnsupportedAccountConfiguration, match="requires a funder"):
            AccountConfiguration.from_values(signature_type=signature_type)


def test_eoa_rejects_separate_funder() -> None:
    with pytest.raises(UnsupportedAccountConfiguration, match="must not specify"):
        AccountConfiguration.from_values(signature_type=0, funder_address="0xfunder")


def test_authenticated_boundary_never_constructs_or_submits_an_order() -> None:
    trading = AuthenticatedPolymarketTrading(
        AccountConfiguration(signature_type=AccountSignatureType.EOA)
    )

    with pytest.raises(AuthenticatedTradingUnavailable, match="disabled"):
        trading.submit(object())
    with pytest.raises(AuthenticatedTradingUnavailable, match="disabled"):
        trading.cancel("order-id")
    with pytest.raises(AuthenticatedTradingUnavailable, match="disabled"):
        trading.cancel_all()
    with pytest.raises(AuthenticatedTradingUnavailable, match="disabled"):
        trading.open_orders()


def test_legacy_bridge_fails_before_authenticated_sdk_use() -> None:
    client = UnsupportedTradingClient()
    args = MarketOrderArgs(token_id="token", amount=1.0, side="BUY", price=0.4)

    with pytest.raises(AuthenticatedTradingUnavailable, match="removed"):
        client.assert_level_1_auth()
    with pytest.raises(AuthenticatedTradingUnavailable, match="removed"):
        client.create_market_order(args)
    with pytest.raises(AuthenticatedTradingUnavailable, match="removed"):
        client.cancel("order-id")
    with pytest.raises(AuthenticatedTradingUnavailable, match="removed"):
        client.cancel_all()
    with pytest.raises(AuthenticatedTradingUnavailable, match="removed"):
        client.get_orders()
