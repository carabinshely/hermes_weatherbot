"""Atomic portfolio-risk evaluation and order-intent persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from weatherbot.domain import (
    EventId,
    LedgerEvent,
    OrderIntentCreated,
    PortfolioValuation,
    PortfolioValuationRecorded,
    RiskDecisionStatus,
    RiskScope,
    RiskScopeRegistered,
    Side,
    fingerprint,
    risk_scope_event_id,
)
from weatherbot.persistence.codec import decode_event, encode_metadata, sha256_text
from weatherbot.persistence.errors import (
    ConcurrentDecisionError,
    CorruptLedgerError,
    DuplicateIntentError,
)
from weatherbot.persistence.store import AppendResult, SQLiteEventStore
from weatherbot.risk import (
    PortfolioRiskDecision,
    PortfolioRiskPolicy,
    PortfolioRiskRejectionReason,
    evaluate_portfolio_risk,
)


@dataclass(frozen=True, slots=True)
class RiskCheckedCommitResult:
    """One atomic risk check plus its optional durable order-intent append."""

    decision: PortfolioRiskDecision | None
    append_result: AppendResult
    committed: bool


def _require_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _valuation_event_id(decision_key: str, valuation: PortfolioValuation) -> EventId:
    material = f"{decision_key}\n{fingerprint(valuation)}"
    return EventId(f"portfolio_valuation_{sha256_text(material)}")


class PortfolioRiskEventStore(SQLiteEventStore):
    """SQLite event store that serializes portfolio risk with BUY intent creation."""

    def commit_order_intent(
        self,
        event: OrderIntentCreated,
        *,
        owner_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> AppendResult:
        """Require the portfolio-risk transaction for new BUY entries."""
        if event.intent.side is Side.BUY:
            raise ValueError(
                "BUY intents require commit_risk_checked_order_intent on PortfolioRiskEventStore"
            )
        return super().commit_order_intent(event, owner_id=owner_id, metadata=metadata)

    def _current_append_result_locked(
        self,
        *,
        duplicate_event_id: str | None = None,
    ) -> AppendResult:
        _, state, last_sequence, tail_hash = self._load_locked()
        return AppendResult(
            appended_sequences=(),
            duplicate_event_ids=(() if duplicate_event_id is None else (duplicate_event_id,)),
            state=state,
            last_sequence=last_sequence,
            chain_hash=tail_hash,
        )

    def commit_risk_checked_order_intent(
        self,
        event: OrderIntentCreated,
        *,
        scope: RiskScope,
        valuation: PortfolioValuation,
        policy: PortfolioRiskPolicy,
        evaluated_at: datetime,
        owner_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> RiskCheckedCommitResult:
        """Re-evaluate current durable risk state and append only if it still permits the BUY."""
        if event.intent.side is not Side.BUY:
            raise ValueError("portfolio risk checked intent commit is only for BUY entries")
        if scope.position_key != (event.intent.market_id, event.intent.outcome_id):
            raise ValueError("risk scope does not match order-intent position key")

        owner_id = _require_text(owner_id, label="owner_id")
        decision_key = _require_text(event.intent.decision_id, label="decision_id")
        _, base_metadata_hash = encode_metadata(metadata)
        now = _utc_now()

        with self._lock, self._transaction():
            row = self._decision_row_locked(decision_key)
            if row is not None:
                claim = self._claim_from_row(row)
                if claim.status == "committed":
                    if claim.intent_id != event.intent.intent_id:
                        raise DuplicateIntentError(
                            f"decision {decision_key!r} already committed intent {claim.intent_id}"
                        )
                    prior = self._intent_event_locked(str(event.intent.intent_id))
                    if prior is None:
                        raise CorruptLedgerError(
                            f"decision {decision_key!r} is committed but its intent event is missing"
                        )
                    prior_event = decode_event(str(prior["payload_json"]))
                    if not isinstance(prior_event, OrderIntentCreated):
                        raise CorruptLedgerError("stored intent row decoded as another event type")
                    if prior_event.intent != event.intent:
                        raise DuplicateIntentError(
                            f"order intent {event.intent.intent_id} was reused with different data"
                        )
                    return RiskCheckedCommitResult(
                        decision=None,
                        append_result=self._current_append_result_locked(
                            duplicate_event_id=str(event.event_id)
                        ),
                        committed=True,
                    )
                if claim.status == "completed":
                    if claim.owner_id != owner_id or claim.intent_id is not None:
                        raise ConcurrentDecisionError(
                            f"decision {decision_key!r} is already completed by another outcome"
                        )
                    return RiskCheckedCommitResult(
                        decision=None,
                        append_result=self._current_append_result_locked(),
                        committed=False,
                    )
                if claim.status != "claimed" or claim.owner_id != owner_id:
                    raise ConcurrentDecisionError(
                        f"decision {decision_key!r} is {claim.status} by owner {claim.owner_id!r}"
                    )
                _, stored_metadata_hash = encode_metadata(claim.metadata)
                if stored_metadata_hash != base_metadata_hash:
                    raise ConcurrentDecisionError(
                        f"decision {decision_key!r} metadata changed between claim and risk commit"
                    )

            events, state, _, _ = self._load_locked()
            decision = evaluate_portfolio_risk(
                state=state,
                events=events,
                proposed_scope=scope,
                proposed_cash=event.intent.cash_reservation,
                valuation=valuation,
                policy=policy,
                evaluated_at=evaluated_at,
            )
            committed_metadata = dict(metadata or {})
            committed_metadata.update(decision.metadata())
            metadata_json, metadata_hash = encode_metadata(committed_metadata)
            valuation_event = PortfolioValuationRecorded(
                event_id=_valuation_event_id(decision_key, valuation),
                occurred_at=valuation.assembled_at,
                valuation=valuation,
            )

            if decision.status is RiskDecisionStatus.REJECTED:
                if row is None:
                    self._connection.execute(
                        """
                        INSERT INTO decision_claims(
                            decision_key, owner_id, status, intent_id, metadata_json,
                            metadata_hash, claimed_at, updated_at
                        )
                        VALUES (?, ?, 'completed', NULL, ?, ?, ?, ?)
                        """,
                        (
                            decision_key,
                            owner_id,
                            metadata_json,
                            metadata_hash,
                            now,
                            now,
                        ),
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE decision_claims
                        SET status = 'completed', intent_id = NULL, metadata_json = ?,
                            metadata_hash = ?, updated_at = ?
                        WHERE decision_key = ?
                        """,
                        (metadata_json, metadata_hash, now, decision_key),
                    )
                invalid_valuation_reasons = {
                    PortfolioRiskRejectionReason.STALE_VALUATION,
                    PortfolioRiskRejectionReason.VALUATION_MISMATCH,
                }
                append_result = (
                    self._current_append_result_locked()
                    if decision.rejection_reason in invalid_valuation_reasons
                    else self._append_events_locked(
                        (valuation_event,),
                        allow_intent_created=False,
                    )
                )
                return RiskCheckedCommitResult(
                    decision=decision,
                    append_result=append_result,
                    committed=False,
                )

            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO decision_claims(
                        decision_key, owner_id, status, intent_id, metadata_json,
                        metadata_hash, claimed_at, updated_at
                    )
                    VALUES (?, ?, 'committed', ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_key,
                        owner_id,
                        str(event.intent.intent_id),
                        metadata_json,
                        metadata_hash,
                        now,
                        now,
                    ),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE decision_claims
                    SET status = 'committed', intent_id = ?, metadata_json = ?,
                        metadata_hash = ?, updated_at = ?
                    WHERE decision_key = ?
                    """,
                    (
                        str(event.intent.intent_id),
                        metadata_json,
                        metadata_hash,
                        now,
                        decision_key,
                    ),
                )

            scope_registered = any(
                isinstance(prior, RiskScopeRegistered)
                and prior.scope.position_key == scope.position_key
                for prior in events
            )
            events_to_append: tuple[LedgerEvent, ...] = (valuation_event, event)
            if not scope_registered:
                scope_event = RiskScopeRegistered(
                    event_id=risk_scope_event_id(scope),
                    occurred_at=evaluated_at,
                    scope=scope,
                )
                events_to_append = (scope_event, valuation_event, event)
            appended = self._append_events_locked(
                events_to_append,
                allow_intent_created=True,
            )
            return RiskCheckedCommitResult(
                decision=decision,
                append_result=appended,
                committed=True,
            )
