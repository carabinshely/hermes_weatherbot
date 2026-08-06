"""Crash-safe SQLite persistence for immutable domain events and recovery."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from weatherbot.domain import (
    DomainError,
    DuplicateEventConflict,
    ExecutionAdapter,
    FillReceived,
    LedgerEvent,
    LedgerState,
    OrderAcknowledged,
    OrderCancelled,
    OrderIntentCreated,
    OrderIntentId,
    OrderOutcomeUnknown,
    OrderRejected,
    OrderState,
    OrderSubmitted,
    apply_event,
)
from weatherbot.domain.events import fingerprint
from weatherbot.persistence.codec import (
    chain_hash,
    decode_event,
    decode_metadata,
    encode_event,
    encode_metadata,
    sha256_text,
)
from weatherbot.persistence.errors import (
    ConcurrentDecisionError,
    CorruptLedgerError,
    DuplicateIntentError,
    PersistenceError,
    RecoveryRequiredError,
    StoreClosedError,
)
from weatherbot.persistence.migrations import (
    CURRENT_SCHEMA_VERSION,
    apply_migrations,
    validate_migrations,
)
from weatherbot.persistence.recovery import (
    AdapterMetadata,
    DecisionClaim,
    PendingDecisionRecovery,
    PendingOrderRecovery,
    RecoveryAction,
    StartupRecovery,
)

GENESIS_CHAIN_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class AppendResult:
    appended_sequences: tuple[int, ...]
    duplicate_event_ids: tuple[str, ...]
    state: LedgerState
    last_sequence: int
    chain_hash: str

    @property
    def appended(self) -> bool:
        return bool(self.appended_sequences)


@dataclass(frozen=True, slots=True)
class DecisionClaimResult:
    claim: DecisionClaim
    created: bool


@dataclass(frozen=True, slots=True)
class StateCheckpoint:
    sequence: int
    chain_hash: str
    state_hash: str
    created_at: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _require_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _row_value(row: sqlite3.Row, key: str) -> object:
    return cast(object, row[key])


def _row_text(row: sqlite3.Row, key: str) -> str:
    value = _row_value(row, key)
    if not isinstance(value, str):
        raise CorruptLedgerError(f"database column {key} must be text")
    return value


def _row_optional_text(row: sqlite3.Row, key: str) -> str | None:
    value = _row_value(row, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CorruptLedgerError(f"database column {key} must be text or null")
    return value


def _row_int(row: sqlite3.Row, key: str) -> int:
    value = _row_value(row, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorruptLedgerError(f"database column {key} must be an integer")
    return value


def _configure_connection(
    connection: sqlite3.Connection,
    *,
    read_only: bool,
    busy_timeout_ms: int,
) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    if read_only:
        connection.execute("PRAGMA query_only = ON")
        return
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA wal_autocheckpoint = 1000")


def _open_connection(
    path: Path,
    *,
    read_only: bool,
    timeout_seconds: float,
) -> sqlite3.Connection:
    if read_only:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            timeout=timeout_seconds,
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
            timeout=timeout_seconds,
        )
    _configure_connection(
        connection,
        read_only=read_only,
        busy_timeout_ms=max(1, int(timeout_seconds * 1000)),
    )
    return connection


def initialize_database(
    path: str | Path,
    *,
    target_version: int = CURRENT_SCHEMA_VERSION,
    timeout_seconds: float = 5.0,
) -> int:
    database_path = Path(path)
    connection = _open_connection(
        database_path,
        read_only=False,
        timeout_seconds=timeout_seconds,
    )
    try:
        return apply_migrations(connection, target_version=target_version)
    finally:
        connection.close()


class SQLiteEventStore:
    """Append-only event store with verified replay as the source of truth."""

    def __init__(
        self,
        path: str | Path,
        *,
        read_only: bool = False,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._path = Path(path)
        self._read_only = read_only
        self._lock = threading.RLock()
        self._closed = False
        self._connection = _open_connection(
            self._path,
            read_only=read_only,
            timeout_seconds=timeout_seconds,
        )
        try:
            if read_only:
                validate_migrations(self._connection, require_current=True)
            else:
                apply_migrations(self._connection)
            self.verify_integrity()
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    @property
    def path(self) -> Path:
        return self._path

    @property
    def read_only(self) -> bool:
        return self._read_only

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreClosedError("event store is closed")

    def _ensure_writable(self) -> None:
        self._ensure_open()
        if self._read_only:
            raise PersistenceError("event store was opened read-only")

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        self._ensure_writable()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.DatabaseError as exc:
            raise PersistenceError(f"could not begin SQLite transaction: {exc}") from exc
        try:
            yield
        except BaseException as exc:
            if self._connection.in_transaction:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.DatabaseError as rollback_exc:
                    raise PersistenceError(
                        "SQLite transaction failed and could not be rolled back"
                    ) from rollback_exc
            raise exc
        try:
            self._connection.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            if self._connection.in_transaction:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.DatabaseError as rollback_exc:
                    raise PersistenceError(
                        "SQLite commit failed and rollback also failed"
                    ) from rollback_exc
            raise PersistenceError(
                f"SQLite commit failed and changes were rolled back: {exc}"
            ) from exc

    def _verify_sqlite_locked(self) -> None:
        try:
            rows = self._connection.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            raise CorruptLedgerError(f"SQLite integrity check failed to run: {exc}") from exc
        messages = [str(row[0]) for row in rows]
        if messages != ["ok"]:
            raise CorruptLedgerError("SQLite integrity check failed: " + "; ".join(messages))

    def _checkpoint_rows_locked(self) -> dict[int, StateCheckpoint]:
        rows = self._connection.execute(
            """
            SELECT sequence, chain_hash, state_hash, created_at
            FROM state_checkpoints
            ORDER BY sequence
            """
        ).fetchall()
        checkpoints: dict[int, StateCheckpoint] = {}
        for raw_row in rows:
            row = cast(sqlite3.Row, raw_row)
            checkpoint = StateCheckpoint(
                sequence=_row_int(row, "sequence"),
                chain_hash=_row_text(row, "chain_hash"),
                state_hash=_row_text(row, "state_hash"),
                created_at=_row_text(row, "created_at"),
            )
            checkpoints[checkpoint.sequence] = checkpoint
        return checkpoints

    def _load_locked(
        self,
        *,
        stop_sequence: int | None = None,
        verify_checkpoints: bool = True,
    ) -> tuple[tuple[LedgerEvent, ...], LedgerState, int, str]:
        parameters: tuple[object, ...] = ()
        where = ""
        if stop_sequence is not None:
            if stop_sequence < 0:
                raise ValueError("stop_sequence must not be negative")
            where = "WHERE sequence <= ?"
            parameters = (stop_sequence,)
        rows = self._connection.execute(
            f"""
            SELECT
                sequence,
                event_id,
                event_type,
                event_schema_version,
                occurred_at,
                intent_id,
                decision_id,
                market_id,
                outcome_id,
                payload_json,
                payload_hash,
                previous_chain_hash,
                chain_hash
            FROM ledger_events
            {where}
            ORDER BY sequence
            """,
            parameters,
        ).fetchall()

        checkpoints = self._checkpoint_rows_locked() if verify_checkpoints else {}
        state = LedgerState.empty()
        events: list[LedgerEvent] = []
        expected_sequence = 1
        previous_hash = GENESIS_CHAIN_HASH

        for raw_row in rows:
            row = cast(sqlite3.Row, raw_row)
            sequence = _row_int(row, "sequence")
            if sequence != expected_sequence:
                raise CorruptLedgerError(
                    f"ledger sequence gap: expected {expected_sequence}, found {sequence}"
                )
            payload_json = _row_text(row, "payload_json")
            stored_payload_hash = _row_text(row, "payload_hash")
            calculated_payload_hash = sha256_text(payload_json)
            if stored_payload_hash != calculated_payload_hash:
                raise CorruptLedgerError(
                    f"ledger event {sequence} payload hash mismatch: expected "
                    f"{stored_payload_hash}, calculated {calculated_payload_hash}"
                )
            stored_previous_hash = _row_text(row, "previous_chain_hash")
            if stored_previous_hash != previous_hash:
                raise CorruptLedgerError(f"ledger event {sequence} chain predecessor mismatch")
            event_type = _row_text(row, "event_type")
            schema_version = _row_int(row, "event_schema_version")
            calculated_chain_hash = chain_hash(
                previous_hash,
                schema_version=schema_version,
                event_type=event_type,
                payload_hash=stored_payload_hash,
            )
            stored_chain_hash = _row_text(row, "chain_hash")
            if stored_chain_hash != calculated_chain_hash:
                raise CorruptLedgerError(
                    f"ledger event {sequence} chain hash mismatch: expected "
                    f"{stored_chain_hash}, calculated {calculated_chain_hash}"
                )
            try:
                event = decode_event(payload_json)
                encoded = encode_event(event)
            except (DomainError, PersistenceError, TypeError, ValueError) as exc:
                raise CorruptLedgerError(
                    f"ledger event {sequence} cannot be decoded safely: {exc}"
                ) from exc
            if encoded.payload_json != payload_json:
                raise CorruptLedgerError(f"ledger event {sequence} payload is not canonical")
            expected_columns = {
                "event_id": encoded.event_id,
                "event_type": encoded.event_type,
                "event_schema_version": encoded.schema_version,
                "occurred_at": encoded.occurred_at,
                "intent_id": encoded.intent_id,
                "decision_id": encoded.decision_id,
                "market_id": encoded.market_id,
                "outcome_id": encoded.outcome_id,
            }
            for column, expected in expected_columns.items():
                if column == "event_schema_version":
                    actual: object = _row_int(row, column)
                else:
                    actual = _row_optional_text(row, column)
                if actual != expected:
                    raise CorruptLedgerError(
                        f"ledger event {sequence} indexed column {column} does not "
                        "match its payload"
                    )
            try:
                state = apply_event(state, event)
            except (DomainError, TypeError, ValueError) as exc:
                raise CorruptLedgerError(
                    f"ledger event {sequence} violates domain replay: {exc}"
                ) from exc
            events.append(event)
            previous_hash = stored_chain_hash
            checkpoint = checkpoints.get(sequence)
            if checkpoint is not None:
                state_hash = fingerprint(state)
                if checkpoint.chain_hash != previous_hash:
                    raise CorruptLedgerError(
                        f"state checkpoint {sequence} references a different chain hash"
                    )
                if checkpoint.state_hash != state_hash:
                    raise CorruptLedgerError(
                        f"state checkpoint {sequence} does not match replayed state"
                    )
            expected_sequence += 1

        last_sequence = expected_sequence - 1
        if stop_sequence is None:
            orphaned = sorted(sequence for sequence in checkpoints if sequence > last_sequence)
            if orphaned:
                raise CorruptLedgerError(
                    f"state checkpoints reference missing ledger sequences: {orphaned}"
                )
        return tuple(events), state, last_sequence, previous_hash

    def _verify_auxiliary_locked(self) -> None:
        try:
            decision_rows = self._connection.execute(
                """
                SELECT decision_key, owner_id, status, intent_id, metadata_json,
                       metadata_hash, claimed_at, updated_at
                FROM decision_claims
                ORDER BY decision_key
                """
            ).fetchall()
            adapter_rows = self._connection.execute(
                """
                SELECT intent_id, backend_name, payload_json, payload_hash,
                       created_at, updated_at
                FROM adapter_metadata
                ORDER BY intent_id
                """
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise CorruptLedgerError(f"cannot read persistence metadata tables: {exc}") from exc

        for raw_row in decision_rows:
            row = cast(sqlite3.Row, raw_row)
            claim = self._claim_from_row(row)
            if claim.status not in {"claimed", "committed", "completed"}:
                raise CorruptLedgerError(
                    f"decision {claim.decision_key!r} has invalid status {claim.status!r}"
                )
            if claim.status != "committed":
                if claim.intent_id is not None:
                    raise CorruptLedgerError(
                        f"decision {claim.decision_key!r} unexpectedly references an intent"
                    )
                continue
            if claim.intent_id is None:
                raise CorruptLedgerError(f"committed decision {claim.decision_key!r} has no intent")
            intent_row = self._intent_event_locked(str(claim.intent_id))
            if intent_row is None:
                raise CorruptLedgerError(
                    f"committed decision {claim.decision_key!r} references missing intent "
                    f"{claim.intent_id}"
                )
            event = decode_event(_row_text(intent_row, "payload_json"))
            if not isinstance(event, OrderIntentCreated):
                raise CorruptLedgerError(
                    f"decision {claim.decision_key!r} references a non-intent event"
                )
            if (
                event.intent.intent_id != claim.intent_id
                or event.intent.decision_id != claim.decision_key
            ):
                raise CorruptLedgerError(
                    f"decision {claim.decision_key!r} disagrees with its intent event"
                )

        for raw_row in adapter_rows:
            row = cast(sqlite3.Row, raw_row)
            adapter = self._adapter_from_row(row)
            try:
                _require_text(adapter.backend_name, label="backend_name")
            except ValueError as exc:
                raise CorruptLedgerError(
                    f"adapter metadata for intent {adapter.intent_id} has a blank backend"
                ) from exc
            if self._intent_event_locked(str(adapter.intent_id)) is None:
                raise CorruptLedgerError(
                    f"adapter metadata references missing intent {adapter.intent_id}"
                )

    def verify_integrity(self) -> None:
        with self._lock:
            self._ensure_open()
            self._verify_sqlite_locked()
            try:
                self._load_locked()
                self._verify_auxiliary_locked()
            except sqlite3.DatabaseError as exc:
                raise CorruptLedgerError(
                    f"persistence schema or ledger query failed: {exc}"
                ) from exc

    def load_events(self) -> tuple[LedgerEvent, ...]:
        with self._lock:
            self._ensure_open()
            events, _, _, _ = self._load_locked()
            return events

    def load_state(self) -> LedgerState:
        with self._lock:
            self._ensure_open()
            _, state, _, _ = self._load_locked()
            return state

    def event_count(self) -> int:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute("SELECT COUNT(*) AS count FROM ledger_events").fetchone()
            if row is None:
                return 0
            return _row_int(cast(sqlite3.Row, row), "count")

    def _existing_event_locked(self, event_id: str) -> sqlite3.Row | None:
        row = self._connection.execute(
            """
            SELECT sequence, event_type, event_schema_version, payload_json, payload_hash,
                   chain_hash
            FROM ledger_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _intent_event_locked(self, intent_id: str) -> sqlite3.Row | None:
        row = self._connection.execute(
            """
            SELECT sequence, event_id, payload_json, payload_hash, chain_hash
            FROM ledger_events
            WHERE event_type = 'order_intent_created' AND intent_id = ?
            """,
            (intent_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def _append_events_locked(
        self,
        events: Iterable[LedgerEvent],
        *,
        allow_intent_created: bool,
    ) -> AppendResult:
        _, state, last_sequence, previous_hash = self._load_locked()
        appended_sequences: list[int] = []
        duplicate_event_ids: list[str] = []

        for event in events:
            if isinstance(event, OrderIntentCreated) and not allow_intent_created:
                raise PersistenceError(
                    "order intents must be persisted with commit_order_intent() so the "
                    "scan decision and intent are committed atomically"
                )
            encoded = encode_event(event)
            existing = self._existing_event_locked(encoded.event_id)
            if existing is not None:
                if (
                    _row_text(existing, "event_type") == encoded.event_type
                    and _row_int(existing, "event_schema_version") == encoded.schema_version
                    and _row_text(existing, "payload_json") == encoded.payload_json
                    and _row_text(existing, "payload_hash") == encoded.payload_hash
                ):
                    duplicate_event_ids.append(encoded.event_id)
                    continue
                raise DuplicateEventConflict(
                    f"event identifier {encoded.event_id} was reused with different data"
                )
            if isinstance(event, OrderIntentCreated):
                prior_intent = self._intent_event_locked(str(event.intent.intent_id))
                if prior_intent is not None:
                    raise DuplicateIntentError(
                        f"order intent {event.intent.intent_id} already exists as event "
                        f"{_row_text(prior_intent, 'event_id')}"
                    )
            try:
                next_state = apply_event(state, event)
            except (DomainError, TypeError, ValueError):
                raise
            sequence = last_sequence + 1
            next_chain_hash = chain_hash(
                previous_hash,
                schema_version=encoded.schema_version,
                event_type=encoded.event_type,
                payload_hash=encoded.payload_hash,
            )
            self._connection.execute(
                """
                INSERT INTO ledger_events(
                    sequence,
                    event_id,
                    event_type,
                    event_schema_version,
                    occurred_at,
                    recorded_at,
                    intent_id,
                    decision_id,
                    market_id,
                    outcome_id,
                    payload_json,
                    payload_hash,
                    previous_chain_hash,
                    chain_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    encoded.event_id,
                    encoded.event_type,
                    encoded.schema_version,
                    encoded.occurred_at,
                    _utc_now(),
                    encoded.intent_id,
                    encoded.decision_id,
                    encoded.market_id,
                    encoded.outcome_id,
                    encoded.payload_json,
                    encoded.payload_hash,
                    previous_hash,
                    next_chain_hash,
                ),
            )
            state = next_state
            last_sequence = sequence
            previous_hash = next_chain_hash
            appended_sequences.append(sequence)

        return AppendResult(
            appended_sequences=tuple(appended_sequences),
            duplicate_event_ids=tuple(duplicate_event_ids),
            state=state,
            last_sequence=last_sequence,
            chain_hash=previous_hash,
        )

    def append(self, event: LedgerEvent) -> AppendResult:
        return self.append_many((event,))

    def append_many(self, events: Iterable[LedgerEvent]) -> AppendResult:
        with self._lock, self._transaction():
            return self._append_events_locked(events, allow_intent_created=False)

    def _claim_from_row(self, row: sqlite3.Row) -> DecisionClaim:
        intent_text = _row_optional_text(row, "intent_id")
        return DecisionClaim(
            decision_key=_row_text(row, "decision_key"),
            owner_id=_row_text(row, "owner_id"),
            status=_row_text(row, "status"),
            intent_id=OrderIntentId(intent_text) if intent_text is not None else None,
            metadata=decode_metadata(
                _row_text(row, "metadata_json"),
                _row_text(row, "metadata_hash"),
            ),
            claimed_at=_row_text(row, "claimed_at"),
            updated_at=_row_text(row, "updated_at"),
        )

    def _decision_row_locked(self, decision_key: str) -> sqlite3.Row | None:
        row = self._connection.execute(
            """
            SELECT decision_key, owner_id, status, intent_id, metadata_json,
                   metadata_hash, claimed_at, updated_at
            FROM decision_claims
            WHERE decision_key = ?
            """,
            (decision_key,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def claim_decision(
        self,
        decision_key: str,
        *,
        owner_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> DecisionClaimResult:
        decision_key = _require_text(decision_key, label="decision_key")
        owner_id = _require_text(owner_id, label="owner_id")
        metadata_json, metadata_hash = encode_metadata(metadata)
        now = _utc_now()
        with self._lock, self._transaction():
            existing = self._decision_row_locked(decision_key)
            if existing is not None:
                claim = self._claim_from_row(existing)
                if (
                    claim.status == "claimed"
                    and claim.owner_id == owner_id
                    and _row_text(existing, "metadata_hash") == metadata_hash
                ):
                    return DecisionClaimResult(claim=claim, created=False)
                raise ConcurrentDecisionError(
                    f"decision {decision_key!r} is already {claim.status} by "
                    f"owner {claim.owner_id!r}"
                )
            self._connection.execute(
                """
                INSERT INTO decision_claims(
                    decision_key, owner_id, status, intent_id, metadata_json,
                    metadata_hash, claimed_at, updated_at
                )
                VALUES (?, ?, 'claimed', NULL, ?, ?, ?, ?)
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
            row = self._decision_row_locked(decision_key)
            if row is None:
                raise CorruptLedgerError("decision claim disappeared after insertion")
            return DecisionClaimResult(claim=self._claim_from_row(row), created=True)

    def complete_decision_without_intent(
        self,
        decision_key: str,
        *,
        owner_id: str,
    ) -> DecisionClaim:
        decision_key = _require_text(decision_key, label="decision_key")
        owner_id = _require_text(owner_id, label="owner_id")
        with self._lock, self._transaction():
            row = self._decision_row_locked(decision_key)
            if row is None:
                raise ConcurrentDecisionError(
                    f"decision {decision_key!r} must be claimed before completion"
                )
            claim = self._claim_from_row(row)
            if claim.status == "completed" and claim.owner_id == owner_id:
                return claim
            if claim.status != "claimed" or claim.owner_id != owner_id:
                raise ConcurrentDecisionError(
                    f"decision {decision_key!r} cannot be completed by owner {owner_id!r}"
                )
            self._connection.execute(
                """
                UPDATE decision_claims
                SET status = 'completed', updated_at = ?
                WHERE decision_key = ?
                """,
                (_utc_now(), decision_key),
            )
            updated = self._decision_row_locked(decision_key)
            if updated is None:
                raise CorruptLedgerError("decision claim disappeared after completion")
            return self._claim_from_row(updated)

    def commit_order_intent(
        self,
        event: OrderIntentCreated,
        *,
        owner_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> AppendResult:
        owner_id = _require_text(owner_id, label="owner_id")
        decision_key = _require_text(event.intent.decision_id, label="decision_id")
        metadata_json, metadata_hash = encode_metadata(metadata)
        now = _utc_now()
        with self._lock, self._transaction():
            row = self._decision_row_locked(decision_key)
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
                claim = self._claim_from_row(row)
                if claim.status == "committed":
                    if claim.intent_id != event.intent.intent_id:
                        raise DuplicateIntentError(
                            f"decision {decision_key!r} already committed intent {claim.intent_id}"
                        )
                    prior = self._intent_event_locked(str(event.intent.intent_id))
                    if prior is None:
                        raise CorruptLedgerError(
                            f"decision {decision_key!r} is committed but its intent event "
                            "is missing"
                        )
                    prior_event = decode_event(_row_text(prior, "payload_json"))
                    if not isinstance(prior_event, OrderIntentCreated):
                        raise CorruptLedgerError("stored intent row decoded as another event type")
                    if prior_event.intent != event.intent:
                        raise DuplicateIntentError(
                            f"order intent {event.intent.intent_id} was reused with different data"
                        )
                    _, state, last_sequence, tail_hash = self._load_locked()
                    return AppendResult(
                        appended_sequences=(),
                        duplicate_event_ids=(str(event.event_id),),
                        state=state,
                        last_sequence=last_sequence,
                        chain_hash=tail_hash,
                    )
                if claim.status != "claimed" or claim.owner_id != owner_id:
                    raise ConcurrentDecisionError(
                        f"decision {decision_key!r} is {claim.status} by owner {claim.owner_id!r}"
                    )
                if _row_text(row, "metadata_hash") != metadata_hash:
                    raise ConcurrentDecisionError(
                        f"decision {decision_key!r} metadata changed between claim and commit"
                    )
                self._connection.execute(
                    """
                    UPDATE decision_claims
                    SET status = 'committed', intent_id = ?, updated_at = ?
                    WHERE decision_key = ?
                    """,
                    (str(event.intent.intent_id), now, decision_key),
                )
            return self._append_events_locked(
                (event,),
                allow_intent_created=True,
            )

    def list_decision_claims(self) -> tuple[DecisionClaim, ...]:
        with self._lock:
            self._ensure_open()
            rows = self._connection.execute(
                """
                SELECT decision_key, owner_id, status, intent_id, metadata_json,
                       metadata_hash, claimed_at, updated_at
                FROM decision_claims
                ORDER BY claimed_at, decision_key
                """
            ).fetchall()
            return tuple(self._claim_from_row(cast(sqlite3.Row, row)) for row in rows)

    def _adapter_from_row(self, row: sqlite3.Row) -> AdapterMetadata:
        return AdapterMetadata(
            intent_id=OrderIntentId(_row_text(row, "intent_id")),
            backend_name=_row_text(row, "backend_name"),
            payload=decode_metadata(
                _row_text(row, "payload_json"),
                _row_text(row, "payload_hash"),
            ),
            created_at=_row_text(row, "created_at"),
            updated_at=_row_text(row, "updated_at"),
        )

    def _adapter_row_locked(self, intent_id: str) -> sqlite3.Row | None:
        row = self._connection.execute(
            """
            SELECT intent_id, backend_name, payload_json, payload_hash,
                   created_at, updated_at
            FROM adapter_metadata
            WHERE intent_id = ?
            """,
            (intent_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def set_adapter_metadata(
        self,
        intent_id: OrderIntentId,
        *,
        backend_name: str,
        payload: Mapping[str, object] | None = None,
    ) -> AdapterMetadata:
        intent_text = _require_text(str(intent_id), label="intent_id")
        backend_name = _require_text(backend_name, label="backend_name")
        payload_json, payload_hash = encode_metadata(payload)
        now = _utc_now()
        with self._lock, self._transaction():
            if self._intent_event_locked(intent_text) is None:
                raise RecoveryRequiredError(
                    f"cannot attach adapter metadata to unknown intent {intent_text}"
                )
            existing = self._adapter_row_locked(intent_text)
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO adapter_metadata(
                        intent_id, backend_name, payload_json, payload_hash,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent_text,
                        backend_name,
                        payload_json,
                        payload_hash,
                        now,
                        now,
                    ),
                )
            else:
                existing_backend = _row_text(existing, "backend_name")
                if existing_backend != backend_name:
                    raise RecoveryRequiredError(
                        f"intent {intent_text} is already assigned to backend {existing_backend!r}"
                    )
                self._connection.execute(
                    """
                    UPDATE adapter_metadata
                    SET payload_json = ?, payload_hash = ?, updated_at = ?
                    WHERE intent_id = ?
                    """,
                    (payload_json, payload_hash, now, intent_text),
                )
            row = self._adapter_row_locked(intent_text)
            if row is None:
                raise CorruptLedgerError("adapter metadata disappeared after write")
            return self._adapter_from_row(row)

    def get_adapter_metadata(
        self,
        intent_id: OrderIntentId,
    ) -> AdapterMetadata | None:
        with self._lock:
            self._ensure_open()
            row = self._adapter_row_locked(str(intent_id))
            return self._adapter_from_row(row) if row is not None else None

    def recover(self) -> StartupRecovery:
        with self._lock:
            self._ensure_open()
            _, state, last_sequence, tail_hash = self._load_locked()
            pending_orders: list[PendingOrderRecovery] = []
            for order in sorted(
                state.orders.values(),
                key=lambda item: str(item.intent.intent_id),
            ):
                if order.state is OrderState.CREATED:
                    action = RecoveryAction.RESUME_SUBMISSION
                elif order.state in {
                    OrderState.SUBMITTED,
                    OrderState.ACKNOWLEDGED,
                    OrderState.PARTIALLY_FILLED,
                    OrderState.UNKNOWN,
                }:
                    action = RecoveryAction.RECONCILE_BACKEND
                else:
                    continue
                adapter_row = self._adapter_row_locked(str(order.intent.intent_id))
                adapter = self._adapter_from_row(adapter_row) if adapter_row is not None else None
                pending_orders.append(
                    PendingOrderRecovery(
                        order=order,
                        action=action,
                        adapter=adapter,
                    )
                )

            pending_decisions = tuple(
                PendingDecisionRecovery(claim=claim)
                for claim in self.list_decision_claims()
                if claim.status == "claimed"
            )
            return StartupRecovery(
                state=state,
                last_sequence=last_sequence,
                chain_hash=tail_hash,
                pending_orders=tuple(pending_orders),
                pending_decisions=pending_decisions,
            )

    def reconcile_startup(
        self,
        resolve_adapter: Callable[[str], ExecutionAdapter],
    ) -> StartupRecovery:
        report = self.recover()
        for pending in report.pending_orders:
            if pending.action is not RecoveryAction.RECONCILE_BACKEND:
                continue
            if pending.adapter is None:
                raise RecoveryRequiredError(
                    f"order {pending.intent_id} is {pending.state.value} but has no "
                    "adapter metadata"
                )
            adapter = resolve_adapter(pending.adapter.backend_name)
            if adapter.backend_name != pending.adapter.backend_name:
                raise RecoveryRequiredError(
                    f"adapter resolver returned {adapter.backend_name!r} for "
                    f"{pending.adapter.backend_name!r}"
                )
            emitted = adapter.reconcile(pending.order)
            for event in emitted:
                if not isinstance(
                    event,
                    (
                        OrderSubmitted,
                        OrderAcknowledged,
                        FillReceived,
                        OrderRejected,
                        OrderCancelled,
                        OrderOutcomeUnknown,
                    ),
                ):
                    raise RecoveryRequiredError(
                        f"adapter {adapter.backend_name!r} emitted unsupported recovery "
                        f"event {type(event).__name__}"
                    )
                if event.intent_id != pending.intent_id:
                    raise RecoveryRequiredError(
                        f"adapter {adapter.backend_name!r} emitted an event for intent "
                        f"{event.intent_id} while reconciling {pending.intent_id}"
                    )
            if emitted:
                self.append_many(emitted)
        return self.recover()

    def create_checkpoint(self) -> StateCheckpoint:
        with self._lock, self._transaction():
            _, state, sequence, tail_hash = self._load_locked()
            if sequence == 0:
                raise PersistenceError("cannot checkpoint an empty ledger")
            state_hash = fingerprint(state)
            existing = self._connection.execute(
                """
                SELECT sequence, chain_hash, state_hash, created_at
                FROM state_checkpoints
                WHERE sequence = ?
                """,
                (sequence,),
            ).fetchone()
            if existing is not None:
                checkpoint = StateCheckpoint(
                    sequence=_row_int(cast(sqlite3.Row, existing), "sequence"),
                    chain_hash=_row_text(cast(sqlite3.Row, existing), "chain_hash"),
                    state_hash=_row_text(cast(sqlite3.Row, existing), "state_hash"),
                    created_at=_row_text(cast(sqlite3.Row, existing), "created_at"),
                )
                if checkpoint.chain_hash != tail_hash or checkpoint.state_hash != state_hash:
                    raise CorruptLedgerError(f"checkpoint {sequence} conflicts with current replay")
                return checkpoint
            created_at = _utc_now()
            self._connection.execute(
                """
                INSERT INTO state_checkpoints(sequence, chain_hash, state_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (sequence, tail_hash, state_hash, created_at),
            )
            return StateCheckpoint(
                sequence=sequence,
                chain_hash=tail_hash,
                state_hash=state_hash,
                created_at=created_at,
            )

    def backup_to(self, destination: str | Path) -> Path:
        destination_path = Path(destination)
        if destination_path.resolve() == self._path.resolve():
            raise ValueError("backup destination must differ from the active database")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_name(f".{destination_path.name}.{uuid.uuid4().hex}.tmp")
        with self._lock:
            self._ensure_open()
            self.verify_integrity()
            backup_connection = sqlite3.connect(temporary)
            try:
                self._connection.backup(backup_connection)
                result = backup_connection.execute("PRAGMA integrity_check").fetchall()
                if [str(row[0]) for row in result] != ["ok"]:
                    raise CorruptLedgerError("new backup failed SQLite integrity verification")
            finally:
                backup_connection.close()
        _fsync_file(temporary)
        os.replace(temporary, destination_path)
        _fsync_directory(destination_path.parent)
        with SQLiteEventStore(destination_path, read_only=True):
            pass
        return destination_path


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore_backup(
    backup_path: str | Path,
    destination: str | Path,
    *,
    timeout_seconds: float = 5.0,
) -> Path:
    source_path = Path(backup_path)
    destination_path = Path(destination)
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("backup source and restore destination must differ")
    with SQLiteEventStore(
        source_path,
        read_only=True,
        timeout_seconds=timeout_seconds,
    ):
        pass

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(
        f".{destination_path.name}.{uuid.uuid4().hex}.restore.tmp"
    )
    source = _open_connection(
        source_path,
        read_only=True,
        timeout_seconds=timeout_seconds,
    )
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    _fsync_file(temporary)
    with SQLiteEventStore(
        temporary,
        read_only=True,
        timeout_seconds=timeout_seconds,
    ):
        pass
    os.replace(temporary, destination_path)
    _fsync_directory(destination_path.parent)
    return destination_path
