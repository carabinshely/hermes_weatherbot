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
        wallet_address="0xproxy",
    )
    safe = AccountConfiguration.from_values(
        signature_type=2,
        wallet_address="0xsafe",
    )
    deposit = AccountConfiguration.from_values(
        signature_type=3,
        wallet_address="0xdeposit",
    )

    assert eoa.signature_type is AccountSignatureType.EOA
    assert proxy.signature_type is AccountSignatureType.POLY_PROXY
    assert safe.signature_type is AccountSignatureType.GNOSIS_SAFE
    assert deposit.signature_type is AccountSignatureType.DEPOSIT_WALLET
    assert deposit.wallet_address == "0xdeposit"


def test_official_wallet_type_mapping_matches_published_sdk() -> None:
    expected = {
        AccountSignatureType.EOA: "EOA",
        AccountSignatureType.POLY_PROXY: "POLY_PROXY",
        AccountSignatureType.GNOSIS_SAFE: "GNOSIS_SAFE",
        AccountSignatureType.DEPOSIT_WALLET: "DEPOSIT_WALLET",
    }

    assert {signature: signature.sdk_wallet_type for signature in expected} == expected


def test_unsupported_signature_type_fails_before_client_construction() -> None:
    with pytest.raises(UnsupportedAccountConfiguration, match="unsupported"):
        AccountConfiguration.from_values(signature_type=99)


def test_non_eoa_modes_require_wallet_address() -> None:
    for signature_type in (1, 2, 3):
        with pytest.raises(UnsupportedAccountConfiguration, match="explicit wallet"):
            AccountConfiguration.from_values(signature_type=signature_type)


def test_eoa_may_defer_wallet_to_future_signer_derivation() -> None:
    configuration = AccountConfiguration.from_values(signature_type=0)

    assert configuration.wallet_address is None


def test_detected_wallet_type_must_match_configured_signature() -> None:
    configuration = AccountConfiguration.from_values(
        signature_type=2,
        wallet_address="0xsafe",
    )

    configuration.require_detected_wallet_type("GNOSIS_SAFE")
    with pytest.raises(UnsupportedAccountConfiguration, match="classified"):
        configuration.require_detected_wallet_type("POLY_PROXY")


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


def test_legacy_bridge_validates_environment_account_configuration() -> None:
    with pytest.raises(UnsupportedAccountConfiguration, match="unsupported"):
        UnsupportedTradingClient(signature_type=99)
    with pytest.raises(UnsupportedAccountConfiguration, match="explicit wallet"):
        UnsupportedTradingClient(signature_type=2)

    client = UnsupportedTradingClient(signature_type=2, wallet_address="0xsafe")
    assert client.configuration.signature_type is AccountSignatureType.GNOSIS_SAFE


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
