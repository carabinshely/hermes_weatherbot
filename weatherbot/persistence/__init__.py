"""Durable, atomic, backend-neutral event storage and startup recovery."""

from weatherbot.persistence.errors import (
    ConcurrentDecisionError,
    CorruptLedgerError,
    DuplicateIntentError,
    MigrationError,
    PersistenceError,
    RecoveryRequiredError,
    SchemaVersionError,
    StoreClosedError,
)
from weatherbot.persistence.migrations import CURRENT_SCHEMA_VERSION
from weatherbot.persistence.recovery import (
    AdapterMetadata,
    DecisionClaim,
    PendingDecisionRecovery,
    PendingOrderRecovery,
    RecoveryAction,
    StartupRecovery,
)
from weatherbot.persistence.risk_store import PortfolioRiskEventStore, RiskCheckedCommitResult
from weatherbot.persistence.store import (
    AppendResult,
    DecisionClaimResult,
    SQLiteEventStore,
    StateCheckpoint,
    initialize_database,
    restore_backup,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AdapterMetadata",
    "AppendResult",
    "ConcurrentDecisionError",
    "CorruptLedgerError",
    "DecisionClaim",
    "DecisionClaimResult",
    "DuplicateIntentError",
    "MigrationError",
    "PendingDecisionRecovery",
    "PendingOrderRecovery",
    "PersistenceError",
    "PortfolioRiskEventStore",
    "RecoveryAction",
    "RecoveryRequiredError",
    "RiskCheckedCommitResult",
    "SQLiteEventStore",
    "SchemaVersionError",
    "StartupRecovery",
    "StateCheckpoint",
    "StoreClosedError",
    "initialize_database",
    "restore_backup",
]
