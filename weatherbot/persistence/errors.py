"""Fail-closed errors for durable event storage and recovery."""


class PersistenceError(RuntimeError):
    """Base class for persistence failures that require explicit handling."""


class StoreClosedError(PersistenceError):
    """Raised when an operation is attempted after the store was closed."""


class SchemaVersionError(PersistenceError):
    """Raised when the on-disk schema cannot be safely interpreted."""


class MigrationError(PersistenceError):
    """Raised when an explicit schema migration cannot be applied or verified."""


class CorruptLedgerError(PersistenceError):
    """Raised when SQLite or ledger-integrity verification detects corruption."""


class DuplicateIntentError(PersistenceError):
    """Raised when one logical order intent is reused with contradictory data."""


class ConcurrentDecisionError(PersistenceError):
    """Raised when another worker owns or completed the same scan decision."""


class RecoveryRequiredError(PersistenceError):
    """Raised when startup cannot safely reconcile an incomplete lifecycle."""
