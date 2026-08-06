from __future__ import annotations

import ast
import sys
from pathlib import Path

from tests.domain.helpers import NOW, buy_intent, event_id
from weatherbot.domain import (
    ExecutionAdapter,
    LedgerEvent,
    Money,
    OrderAggregate,
    OrderSubmitted,
    PreTradeDecision,
    RiskDecisionStatus,
    Signal,
)


class FakePaperAdapter:
    @property
    def backend_name(self) -> str:
        return "paper"

    def submit(self, intent: object) -> tuple[LedgerEvent, ...]:
        order_intent = buy_intent() if not hasattr(intent, "intent_id") else intent
        return (
            OrderSubmitted(
                event_id=event_id("paper-submit"),
                occurred_at=NOW,
                intent_id=order_intent.intent_id,  # type: ignore[attr-defined]
                backend_order_id="paper-1",
            ),
        )

    def cancel(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return ()

    def reconcile(self, order: OrderAggregate) -> tuple[LedgerEvent, ...]:
        return ()


class FakeLiveAdapter(FakePaperAdapter):
    @property
    def backend_name(self) -> str:
        return "live"



def test_paper_and_live_backends_share_one_execution_protocol() -> None:
    assert isinstance(FakePaperAdapter(), ExecutionAdapter)
    assert isinstance(FakeLiveAdapter(), ExecutionAdapter)


def test_risk_decision_contract_is_explicit() -> None:
    intent = buy_intent()
    signal = Signal(
        strategy_id=intent.strategy_id,
        decision_id=intent.decision_id,
        market_id=intent.market_id,
        outcome_id=intent.outcome_id,
        probability=intent.limit_price,
        observed_price=intent.limit_price,
        generated_at=NOW,
    )

    approved = PreTradeDecision(
        signal=signal,
        status=RiskDecisionStatus.APPROVED,
        max_cash=Money.of("5"),
        reason="within configured limits",
    )
    rejected = PreTradeDecision(
        signal=signal,
        status=RiskDecisionStatus.REJECTED,
        max_cash=Money.zero(),
        reason="daily loss limit reached",
    )

    assert approved.status is RiskDecisionStatus.APPROVED
    assert rejected.max_cash.is_zero


def test_domain_package_imports_no_exchange_wallet_or_network_sdk() -> None:
    forbidden_roots = {"py_clob_client", "web3", "eth_account", "requests", "dotenv"}
    domain_root = Path("weatherbot/domain")

    for path in domain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

        unexpected = {
            root
            for root in imported_roots
            if root not in sys.stdlib_module_names and root != "weatherbot"
        }
        assert not (imported_roots & forbidden_roots), path
        assert not unexpected, f"{path} imports non-domain dependency roots: {unexpected}"
