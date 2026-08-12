"""Paper-ledger lifecycle: explicit initialization, archive, and reset only."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from weatherbot.domain import AccountOpened, EventId, Money
from weatherbot.persistence import PortfolioRiskEventStore


def _account_event_id(starting_cash: Money) -> EventId:
    material = f"paper-account\n{starting_cash.currency}\n{starting_cash.amount}".encode()
    return EventId(f"paper_account_opened_{hashlib.sha256(material).hexdigest()}")


def initialize_paper_store(
    path: str | Path,
    *,
    starting_cash: Money,
    opened_at: datetime | None = None,
) -> PortfolioRiskEventStore:
    """Open a paper ledger, creating its initial cash event only when truly empty."""
    if starting_cash.amount <= 0:
        raise ValueError("paper starting cash must be positive")
    timestamp = opened_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("paper ledger opening time must be timezone-aware")
    store = PortfolioRiskEventStore(path)
    try:
        if store.event_count() == 0:
            store.append(
                AccountOpened(
                    event_id=_account_event_id(starting_cash),
                    occurred_at=timestamp,
                    initial_cash=starting_cash,
                )
            )
            return store
        store.verify_integrity()
        account_events = tuple(
            event for event in store.load_events() if isinstance(event, AccountOpened)
        )
        if len(account_events) != 1:
            raise ValueError("paper ledger must contain exactly one account-opening event")
        if account_events[0].initial_cash != starting_cash:
            raise ValueError(
                "configured paper starting cash differs from durable ledger; "
                "use the explicit archive/reset command instead of silently resetting history"
            )
        return store
    except Exception:
        store.close()
        raise


def archive_and_reset_paper_ledger(
    path: str | Path,
    *,
    archive_directory: str | Path,
    starting_cash: Money,
    reset_at: datetime | None = None,
) -> Path:
    """Archive a verified paper ledger, then create a new ledger explicitly."""
    timestamp = reset_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("paper reset time must be timezone-aware")
    ledger_path = Path(path)
    archive_dir = Path(archive_directory)
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = archive_dir / f"paper-ledger-{stamp}.sqlite3"

    store = PortfolioRiskEventStore(ledger_path)
    try:
        if store.event_count() == 0:
            raise ValueError("cannot archive/reset an empty paper ledger")
        store.verify_integrity()
        store.backup_to(archive_path)
    finally:
        store.close()

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{ledger_path}{suffix}")
        if candidate.exists():
            candidate.unlink()

    fresh = initialize_paper_store(
        ledger_path,
        starting_cash=starting_cash,
        opened_at=timestamp,
    )
    fresh.close()
    return archive_path
