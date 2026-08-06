"""Poll final Polymarket UMA payouts through the public Gamma API."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol, cast

import requests

from weatherbot.domain import MarketId, MarketResolution, OutcomeId, OutcomePayout
from weatherbot.domain.resolution import (
    MarketResolutionEvidence,
    ResolutionEvidenceStatus,
)
from weatherbot.markets import (
    BinaryOutcome,
    GammaMarketError,
    MarketCalendar,
    parse_gamma_binary_market,
    parse_temperature_bucket,
)
from weatherbot.resolution.model import (
    ResolutionContext,
    ResolutionPollResult,
    ResolutionPollStatus,
)

GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets/{market_id}"


class ResolutionSourceUnavailable(RuntimeError):
    """Raised when an authoritative public source cannot be reached safely."""


class GammaResolutionTransport(Protocol):
    def get_market(self, market_id: str) -> Mapping[str, object]: ...


@dataclass(slots=True)
class RequestsGammaResolutionTransport:
    timeout_seconds: float = 15.0

    def get_market(self, market_id: str) -> Mapping[str, object]:
        url = GAMMA_MARKET_URL.format(market_id=market_id)
        try:
            response = requests.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = cast(object, response.json())
        except (requests.RequestException, ValueError) as exc:
            raise ResolutionSourceUnavailable(f"Gamma request failed: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ResolutionSourceUnavailable("Gamma market response is not an object")
        return cast(Mapping[str, object], payload)


def _aware_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GammaMarketError(f"{label} must be a non-blank timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GammaMarketError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GammaMarketError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _finalized_at(payload: Mapping[str, object]) -> datetime:
    for key in ("closedTime", "updatedAt", "umaEndDate"):
        value = payload.get(key)
        if value not in (None, ""):
            return _aware_datetime(value, label=key)
    raise GammaMarketError("closed market has no authoritative finalization timestamp")


def _status_text(payload: Mapping[str, object]) -> str:
    value = payload.get("umaResolutionStatus")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GammaMarketError("umaResolutionStatus must be text when present")
    return value.strip().casefold()


def _payload_hash(payload: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GammaMarketError(f"Gamma payload is not canonical JSON data: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _poll_result(
    context: ResolutionContext,
    status: ResolutionPollStatus,
    checked_at: datetime,
    reason: str,
) -> ResolutionPollResult:
    return ResolutionPollResult(
        market_id=context.market_id,
        status=status,
        checked_at=checked_at,
        reason=reason,
    )


@dataclass(slots=True)
class GammaResolutionSource:
    transport: GammaResolutionTransport
    delay_grace: timedelta = timedelta(hours=2)

    def poll(
        self,
        context: ResolutionContext,
        *,
        checked_at: datetime | None = None,
    ) -> ResolutionPollResult:
        checked = checked_at or datetime.now(UTC)
        if checked.tzinfo is None or checked.utcoffset() is None:
            raise ValueError("checked_at must be timezone-aware")
        checked = checked.astimezone(UTC)
        try:
            payload = self.transport.get_market(str(context.market_id))
        except ResolutionSourceUnavailable as exc:
            return _poll_result(
                context,
                ResolutionPollStatus.UNAVAILABLE,
                checked,
                str(exc),
            )

        try:
            market = parse_gamma_binary_market(payload)
            if str(market.identity.market_id) != str(context.market_id):
                raise GammaMarketError("Gamma response returned a different market id")
            if market.identity.condition_id != context.condition_id:
                raise GammaMarketError("Gamma condition id differs from persisted context")
            bucket = parse_temperature_bucket(market.question)
            if bucket != context.bucket:
                raise GammaMarketError("market temperature bucket differs from signal context")
            if market.end_at is not None:
                local_end_date = MarketCalendar(context.market_timezone).local_date(market.end_at)
                if local_end_date != context.market_date:
                    raise GammaMarketError(
                        "market end timestamp does not match persisted local market date"
                    )
            declared_source = market.resolution_source
            if declared_source is None:
                raise GammaMarketError("market has no declared resolution source")
            if context.declared_resolution_source is not None and declared_source.rstrip(
                "/"
            ) != context.declared_resolution_source.rstrip("/"):
                raise GammaMarketError(
                    "declared resolution source changed after the signal was recorded"
                )

            status_text = _status_text(payload)
            if "disput" in status_text:
                return _poll_result(
                    context,
                    ResolutionPollStatus.DISPUTED,
                    checked,
                    f"UMA resolution is disputed ({status_text})",
                )
            if not market.closed:
                if market.end_at is not None and checked >= market.end_at + self.delay_grace:
                    return _poll_result(
                        context,
                        ResolutionPollStatus.DELAYED,
                        checked,
                        "market passed its end time but has no final UMA payout",
                    )
                return _poll_result(
                    context,
                    ResolutionPollStatus.PENDING,
                    checked,
                    "market is not closed",
                )

            yes = market.descriptive_price(BinaryOutcome.YES)
            no = market.descriptive_price(BinaryOutcome.NO)
            if yes is None or no is None:
                raise GammaMarketError("closed market is missing final outcome payouts")
            if yes + no != Decimal("1"):
                raise GammaMarketError("final binary payouts do not sum to one")
            if (yes, no) == (Decimal("0.5"), Decimal("0.5")):
                evidence_status = ResolutionEvidenceStatus.VOID
                poll_status = ResolutionPollStatus.VOID
            elif sorted((yes, no)) == [Decimal("0"), Decimal("1")]:
                evidence_status = ResolutionEvidenceStatus.VERIFIED
                poll_status = ResolutionPollStatus.FINAL
            else:
                raise GammaMarketError(
                    f"closed market has unsupported payout vector YES={yes}, NO={no}"
                )

            yes_token = market.identity.token_for(BinaryOutcome.YES)
            no_token = market.identity.token_for(BinaryOutcome.NO)
            payouts = (
                OutcomePayout(outcome_id=OutcomeId(str(yes_token)), payout=yes),
                OutcomePayout(outcome_id=OutcomeId(str(no_token)), payout=no),
            )
            finalized = _finalized_at(payload)
            if finalized > checked:
                raise GammaMarketError("market finalization timestamp is in the future")
            payload_hash = _payload_hash(payload)
            resolution_value = json.dumps(
                {"NO": format(no, "f"), "YES": format(yes, "f")},
                sort_keys=True,
                separators=(",", ":"),
            )
            evidence = MarketResolutionEvidence(
                market_id=context.market_id,
                condition_id=str(context.condition_id),
                source_name="Polymarket UMA final payout",
                source_url=GAMMA_MARKET_URL.format(market_id=context.market_id),
                declared_resolution_source=declared_source,
                retrieved_at=checked,
                finalized_at=finalized,
                market_date=context.market_date,
                market_timezone=context.market_timezone,
                status=evidence_status,
                resolution_value=resolution_value,
                payouts=payouts,
                payload_hash=payload_hash,
            )
            resolution = MarketResolution(
                market_id=MarketId(str(context.market_id)),
                payouts=payouts,
                resolved_at=finalized,
            )
            return ResolutionPollResult(
                market_id=context.market_id,
                status=poll_status,
                checked_at=checked,
                reason=(
                    "market resolved to a verified binary winner"
                    if poll_status is ResolutionPollStatus.FINAL
                    else "market resolved 0.5/0.5 as void or unknown"
                ),
                evidence=evidence,
                resolution=resolution,
            )
        except (GammaMarketError, ValueError) as exc:
            return _poll_result(
                context,
                ResolutionPollStatus.MALFORMED,
                checked,
                str(exc),
            )
