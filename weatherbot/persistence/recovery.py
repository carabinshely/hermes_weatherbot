"""Backend-neutral startup recovery plans derived from durable ledger state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from weatherbot.domain import LedgerState, OrderAggregate, OrderIntentId, OrderState

if TYPE_CHECKING:
    from collections.abc import Mapping


class RecoveryAction(StrEnum):
    RESUME_SUBMISSION = "resume_submission"
    RECONCILE_BACKEND = "reconcile_backend"
    RETRY_DECISION = "retry_decision"


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    intent_id: OrderIntentId
    backend_name: str
    payload: Mapping[str, object]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DecisionClaim:
    decision_key: str
    owner_id: str
    status: str
    intent_id: OrderIntentId | None
    metadata: Mapping[str, object]
    claimed_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PendingOrderRecovery:
    order: OrderAggregate
    action: RecoveryAction
    adapter: AdapterMetadata | None

    @property
    def intent_id(self) -> OrderIntentId:
        return self.order.intent.intent_id

    @property
    def state(self) -> OrderState:
        return self.order.state


@dataclass(frozen=True, slots=True)
class PendingDecisionRecovery:
    claim: DecisionClaim
    action: RecoveryAction = RecoveryAction.RETRY_DECISION


@dataclass(frozen=True, slots=True)
class StartupRecovery:
    state: LedgerState
    last_sequence: int
    chain_hash: str
    pending_orders: tuple[PendingOrderRecovery, ...]
    pending_decisions: tuple[PendingDecisionRecovery, ...]

    @property
    def is_clean(self) -> bool:
        return not self.pending_orders and not self.pending_decisions

    @property
    def backend_reconciliation_required(self) -> bool:
        return any(
            item.action is RecoveryAction.RECONCILE_BACKEND
            for item in self.pending_orders
        )
