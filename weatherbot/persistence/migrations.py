"""Explicit, checksummed SQLite schema migrations for the event store."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from weatherbot.persistence.errors import MigrationError, SchemaVersionError


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        material = "\n-- statement --\n".join(statement.strip() for statement in self.statements)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="immutable event ledger and decision claims",
        statements=(
            """
            CREATE TABLE ledger_events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                event_schema_version INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                intent_id TEXT,
                decision_id TEXT,
                market_id TEXT,
                outcome_id TEXT,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                previous_chain_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL UNIQUE,
                CHECK (event_schema_version > 0),
                CHECK (length(payload_hash) = 64),
                CHECK (length(previous_chain_hash) = 64),
                CHECK (length(chain_hash) = 64)
            )
            """,
            """
            CREATE UNIQUE INDEX uq_ledger_order_intent
            ON ledger_events(intent_id)
            WHERE event_type = 'order_intent_created'
            """,
            """
            CREATE UNIQUE INDEX uq_ledger_order_decision
            ON ledger_events(decision_id)
            WHERE event_type = 'order_intent_created'
            """,
            """
            CREATE INDEX ix_ledger_intent_sequence
            ON ledger_events(intent_id, sequence)
            """,
            """
            CREATE INDEX ix_ledger_market_sequence
            ON ledger_events(market_id, sequence)
            """,
            """
            CREATE TABLE decision_claims (
                decision_key TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                status TEXT NOT NULL,
                intent_id TEXT UNIQUE,
                metadata_json TEXT NOT NULL,
                metadata_hash TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (status IN ('claimed', 'committed', 'completed')),
                CHECK (
                    (status = 'committed' AND intent_id IS NOT NULL)
                    OR (status != 'committed' AND intent_id IS NULL)
                ),
                CHECK (length(metadata_hash) = 64)
            )
            """,
            """
            CREATE INDEX ix_decision_claim_status
            ON decision_claims(status, claimed_at)
            """,
        ),
    ),
    Migration(
        version=2,
        name="adapter metadata and verified state checkpoints",
        statements=(
            """
            CREATE TABLE adapter_metadata (
                intent_id TEXT PRIMARY KEY,
                backend_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (length(payload_hash) = 64)
            )
            """,
            """
            CREATE TABLE state_checkpoints (
                sequence INTEGER PRIMARY KEY,
                chain_hash TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK (sequence > 0),
                CHECK (length(chain_hash) = 64),
                CHECK (length(state_hash) = 64)
            )
            """,
        ),
    ),
)

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version


def _migration_for(version: int) -> Migration:
    try:
        return next(migration for migration in MIGRATIONS if migration.version == version)
    except StopIteration as exc:
        raise SchemaVersionError(f"no migration is defined for schema version {version}") from exc


def _begin_immediate(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.execute("ROLLBACK")


def _bootstrap_migration_table(connection: sqlite3.Connection) -> None:
    _begin_immediate(connection)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                CHECK (version > 0),
                CHECK (length(checksum) = 64)
            )
            """
        )
        connection.execute("COMMIT")
    except sqlite3.DatabaseError as exc:
        _rollback(connection)
        raise MigrationError(f"could not bootstrap schema migrations: {exc}") from exc


def applied_schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def validate_migrations(
    connection: sqlite3.Connection,
    *,
    require_current: bool,
) -> int:
    try:
        rows = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise SchemaVersionError(f"cannot read schema migration history: {exc}") from exc

    versions = [int(row[0]) for row in rows]
    if versions and versions != list(range(1, max(versions) + 1)):
        raise SchemaVersionError(f"schema migration history contains gaps: {versions}")

    for row in rows:
        version = int(row[0])
        name = str(row[1])
        checksum = str(row[2])
        if version > CURRENT_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database schema version {version} is newer than supported version "
                f"{CURRENT_SCHEMA_VERSION}"
            )
        expected = _migration_for(version)
        if name != expected.name or checksum != expected.checksum:
            raise SchemaVersionError(
                f"schema migration {version} does not match the application definition"
            )

    current = max(versions, default=0)
    if require_current and current != CURRENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema version {current} requires migration to {CURRENT_SCHEMA_VERSION}"
        )
    return current


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    target_version: int = CURRENT_SCHEMA_VERSION,
) -> int:
    if target_version < 0 or target_version > CURRENT_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"target schema version must be between 0 and {CURRENT_SCHEMA_VERSION}"
        )
    _bootstrap_migration_table(connection)
    current = validate_migrations(connection, require_current=False)
    if current > target_version:
        raise SchemaVersionError(
            f"database is already at schema version {current}; downgrade to {target_version} "
            "is not supported"
        )

    for migration in MIGRATIONS:
        if not current < migration.version <= target_version:
            continue
        _begin_immediate(connection)
        try:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            _rollback(connection)
            raise MigrationError(
                f"migration {migration.version} ({migration.name}) failed: {exc}"
            ) from exc
        current = migration.version

    validate_migrations(
        connection,
        require_current=target_version == CURRENT_SCHEMA_VERSION,
    )
    return current
