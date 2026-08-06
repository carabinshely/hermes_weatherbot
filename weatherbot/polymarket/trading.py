"""Authenticated Polymarket boundary.

The official SDK dependency is installed, but funded-wallet operations remain disabled
until identifier mapping, order validation, and reconciliation are implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from weatherbot.polymarket.errors import (
    AuthenticatedTradingUnavailable,
    UnsupportedAccountConfiguration,
)


class AccountSignatureType(IntEnum):
    """Signature values used by the published official SDK."""

    EOA = 0
    POLY_PROXY = 1
    GNOSIS_SAFE = 2
    DEPOSIT_WALLET = 3

    @property
    def sdk_wallet_type(self) -> str:
        return {
            AccountSignatureType.EOA: "EOA",
            AccountSignatureType.POLY_PROXY: "POLY_PROXY",
            AccountSignatureType.GNOSIS_SAFE: "GNOSIS_SAFE",
            AccountSignatureType.DEPOSIT_WALLET: "DEPOSIT_WALLET",
        }[self]


@dataclass(frozen=True, slots=True)
class AccountConfiguration:
    """Expected wallet classification for a future secure SDK client.

    The official SDK derives the signature type from the signer and requested wallet.
    This configuration records the expected result so a future live adapter can reject
    a mismatch before constructing or posting an order.
    """

    signature_type: AccountSignatureType
    wallet_address: str | None = None

    def __post_init__(self) -> None:
        normalized_wallet = self.wallet_address.strip() if self.wallet_address else None
        if self.signature_type is not AccountSignatureType.EOA and normalized_wallet is None:
            raise UnsupportedAccountConfiguration(
                f"{self.signature_type.name} signing requires an explicit wallet address"
            )
        object.__setattr__(self, "wallet_address", normalized_wallet)

    @classmethod
    def from_values(
        cls,
        *,
        signature_type: int,
        wallet_address: str | None = None,
    ) -> AccountConfiguration:
        try:
            parsed = AccountSignatureType(signature_type)
        except ValueError as exc:
            raise UnsupportedAccountConfiguration(
                f"unsupported Polymarket signature type: {signature_type}"
            ) from exc
        return cls(signature_type=parsed, wallet_address=wallet_address)

    def require_detected_wallet_type(self, detected_wallet_type: str) -> None:
        """Reject an SDK-classified wallet that disagrees with configuration."""
        normalized = detected_wallet_type.strip().upper()
        expected = self.signature_type.sdk_wallet_type
        if normalized != expected:
            raise UnsupportedAccountConfiguration(
                f"configured signature type expects {expected}, but the official SDK "
                f"classified the wallet as {normalized or '<blank>'}"
            )


class AuthenticatedPolymarketTrading:
    """Fail-closed placeholder for the future official-SDK secure adapter."""

    def __init__(self, configuration: AccountConfiguration) -> None:
        self.configuration = configuration

    @staticmethod
    def _blocked(operation: str) -> AuthenticatedTradingUnavailable:
        return AuthenticatedTradingUnavailable(
            f"Polymarket {operation} is disabled: the official secure adapter is not "
            "implemented and funded-wallet operation remains unsupported"
        )

    def submit(self, *_args: object, **_kwargs: object) -> None:
        raise self._blocked("order submission")

    def cancel(self, *_args: object, **_kwargs: object) -> None:
        raise self._blocked("order cancellation")

    def cancel_all(self) -> None:
        raise self._blocked("bulk cancellation")

    def open_orders(self) -> None:
        raise self._blocked("authenticated order listing")
