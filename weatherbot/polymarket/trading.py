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
    EOA = 0
    POLY_PROXY = 1
    POLY_GNOSIS_SAFE = 2
    POLY_1271 = 3


@dataclass(frozen=True, slots=True)
class AccountConfiguration:
    signature_type: AccountSignatureType
    funder_address: str | None = None

    def __post_init__(self) -> None:
        if self.signature_type is AccountSignatureType.EOA:
            if self.funder_address is not None:
                raise UnsupportedAccountConfiguration(
                    "EOA signing must not specify a separate funder address"
                )
            return
        if self.funder_address is None or not self.funder_address.strip():
            raise UnsupportedAccountConfiguration(
                f"{self.signature_type.name} signing requires a funder address"
            )

    @classmethod
    def from_values(
        cls,
        *,
        signature_type: int,
        funder_address: str | None = None,
    ) -> AccountConfiguration:
        try:
            parsed = AccountSignatureType(signature_type)
        except ValueError as exc:
            raise UnsupportedAccountConfiguration(
                f"unsupported Polymarket signature type: {signature_type}"
            ) from exc
        normalized_funder = funder_address.strip() if funder_address else None
        return cls(signature_type=parsed, funder_address=normalized_funder)


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
