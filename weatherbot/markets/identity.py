"""Strongly typed Polymarket market and outcome identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_CONDITION_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_DECIMAL_ID_RE = re.compile(r"^[0-9]+$")


class MarketIdentityError(ValueError):
    """Raised when market identity fields are missing, malformed, or ambiguous."""


def _require_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise MarketIdentityError(f"{label} must not be blank")
    return normalized


@dataclass(frozen=True, slots=True, order=True)
class GammaMarketId:
    value: str

    def __post_init__(self) -> None:
        normalized = _require_text(self.value, label="market id")
        if not _DECIMAL_ID_RE.fullmatch(normalized):
            raise MarketIdentityError("market id must be a decimal Gamma market identifier")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class ConditionId:
    value: str

    def __post_init__(self) -> None:
        normalized = _require_text(self.value, label="condition id")
        if not _CONDITION_RE.fullmatch(normalized):
            raise MarketIdentityError(
                "condition id must be a 32-byte 0x-prefixed hexadecimal identifier"
            )
        object.__setattr__(self, "value", normalized.lower())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class OutcomeTokenId:
    value: str

    def __post_init__(self) -> None:
        normalized = _require_text(self.value, label="outcome token id")
        if not _DECIMAL_ID_RE.fullmatch(normalized):
            raise MarketIdentityError("outcome token id must be a decimal asset identifier")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


class BinaryOutcome(StrEnum):
    YES = "yes"
    NO = "no"

    @classmethod
    def parse(cls, value: str) -> BinaryOutcome:
        normalized = _require_text(value, label="outcome").casefold()
        aliases = {
            "yes": cls.YES,
            "y": cls.YES,
            "no": cls.NO,
            "n": cls.NO,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise MarketIdentityError(f"unsupported binary outcome label: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class OutcomeToken:
    outcome: BinaryOutcome
    token_id: OutcomeTokenId


@dataclass(frozen=True, slots=True)
class BinaryMarketIdentity:
    market_id: GammaMarketId
    condition_id: ConditionId
    outcome_tokens: tuple[OutcomeToken, OutcomeToken]

    def __post_init__(self) -> None:
        outcomes = [entry.outcome for entry in self.outcome_tokens]
        token_ids = [entry.token_id for entry in self.outcome_tokens]
        if set(outcomes) != {BinaryOutcome.YES, BinaryOutcome.NO}:
            raise MarketIdentityError("binary market must map exactly one YES and one NO outcome")
        if len(set(token_ids)) != 2:
            raise MarketIdentityError("YES and NO outcomes must have distinct token ids")

    def token_for(self, outcome: BinaryOutcome) -> OutcomeTokenId:
        for entry in self.outcome_tokens:
            if entry.outcome is outcome:
                return entry.token_id
        raise MarketIdentityError(f"market has no token mapping for {outcome.value}")

    def select(self, outcome: BinaryOutcome) -> MarketSelection:
        return MarketSelection(
            market_id=self.market_id,
            condition_id=self.condition_id,
            outcome=outcome,
            token_id=self.token_for(outcome),
        )


@dataclass(frozen=True, slots=True)
class MarketSelection:
    market_id: GammaMarketId
    condition_id: ConditionId
    outcome: BinaryOutcome
    token_id: OutcomeTokenId

    @property
    def log_label(self) -> str:
        return (
            f"market={self.market_id} condition={self.condition_id} "
            f"outcome={self.outcome.value.upper()} token={self.token_id}"
        )
