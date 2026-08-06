"""Backend-neutral ports implemented independently by paper and live adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from weatherbot.domain.events import LedgerEvent
from weatherbot.domain.model import OrderAggregate, OrderIntent


@runtime_checkable
class ExecutionAdapter(Protocol):
    """Common execution contract for paper and live backends.

    Adapters report facts as immutable events. They may not mutate ledger balances,
    positions, or order aggregates directly.
    """

    @property
    def backend_name(self) -> str: ...

    def submit(self, intent: OrderIntent) -> tuple[LedgerEvent, ...]: ...

    def cancel(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]: ...

    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]: ...
