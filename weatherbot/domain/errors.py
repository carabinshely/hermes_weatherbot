"""Domain-layer errors.

These exceptions are intentionally independent from exchange SDKs and transport errors.
"""


class DomainError(RuntimeError):
    """Base class for fail-closed domain failures."""


class InvalidTransition(DomainError):
    """Raised when an aggregate receives an event that is invalid for its state."""


class InvariantViolation(DomainError):
    """Raised when applying an event would violate a financial invariant."""


class DuplicateEventConflict(DomainError):
    """Raised when an existing event or fill identifier is reused with new data."""


class AggregateNotFound(DomainError):
    """Raised when an event references a missing order, position, or resolution."""
