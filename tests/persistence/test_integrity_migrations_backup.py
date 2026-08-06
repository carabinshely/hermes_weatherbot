from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.domain.helpers import account_opened
from tests.persistence.helpers import (
    fill,
    intent_created,
    market_resolved,
    position_settled,
    submitted,
)
from weatherbot.domain import Money
from weatherbot.persistence import (
    CURRENT_SCHEMA_VERSION,
    CorruptLedgerError,
    PersistenceError,
    SQLiteEventStore,
    SchemaVersionError,
    initialize_database,
    restore_backup,
)


def test_schema_upgrade_from_version_one_is_explicit_and_replay_safe(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.sqlite3"
    assert initialize_database(database, target_version=1) == 1

    connection = sqlite3.connect(database)
    try:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        assert version == (1,)
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            connection.execute("SELECT * FROM adapter_metadata").fetchall()
    finally:
        connection.close()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        assert store.load_state().cash == Money.of("100")

    connection = sqlite3.connect(database)
    try:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert versions == [(1,), (CURRENT_SCHEMA_VERSION,)]
        connection.execute("SELECT * FROM adapter_metadata").fetchall()
        connection.execute("SELECT * FROM state_checkpoints").fetchall()
    finally:
        connection.close()


def test_migration_checksum_tampering_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "migration-checksum.sqlite3"
    with SQLiteEventStore(database):
        pass

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
            ("0" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SchemaVersionError, match="does not match"):
        SQLiteEventStore(database)


def test_payload_tampering_is_detected_before_replay(tmp_path: Path) -> None:
    database = tmp_path / "tampered-payload.sqlite3"
    with SQLiteEventStore(database) as store:
        store.append(account_opened())

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE ledger_events SET payload_json = payload_json || ' ' WHERE sequence = 1"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CorruptLedgerError, match="payload hash mismatch"):
        SQLiteEventStore(database)


def test_hash_chain_detects_rewritten_event_even_with_updated_payload_hash(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tampered-chain.sqlite3"
    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.create_checkpoint()

    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT payload_json FROM ledger_events WHERE sequence = 1"
        ).fetchone()
        assert row is not None
        payload = str(row[0]).replace('"100.000000"', '"200.000000"')
        import hashlib

        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        connection.execute(
            """
            UPDATE ledger_events
            SET payload_json = ?, payload_hash = ?
            WHERE sequence = 1
            """,
            (payload, payload_hash),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CorruptLedgerError, match="chain hash mismatch"):
        SQLiteEventStore(database)


def test_checkpoint_tampering_is_detected(tmp_path: Path) -> None:
    database = tmp_path / "tampered-checkpoint.sqlite3"
    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.create_checkpoint()

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE state_checkpoints SET state_hash = ? WHERE sequence = 1",
            ("f" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(CorruptLedgerError, match="does not match replayed state"):
        SQLiteEventStore(database)


def test_backup_and_restore_preserve_a_verified_point_in_time(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite3"
    backup = tmp_path / "backups" / "ledger-backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    intent = intent_created()

    with SQLiteEventStore(database) as store:
        store.append(account_opened())
        store.commit_order_intent(intent, owner_id="worker-a")
        store.append_many((submitted(intent), fill(intent)))
        expected_backup_state = store.load_state()
        store.create_checkpoint()
        store.backup_to(backup)

        store.append_many((market_resolved(), position_settled()))
        assert store.load_state().cash != expected_backup_state.cash

    restore_backup(backup, restored)

    with SQLiteEventStore(restored, read_only=True) as store:
        assert store.load_state() == expected_backup_state
        assert store.event_count() == 4
        with pytest.raises(PersistenceError, match="read-only"):
            store.append(account_opened())


def test_persistence_package_has_no_exchange_wallet_or_network_imports() -> None:
    forbidden_roots = {"py_clob_client", "web3", "eth_account", "requests", "dotenv"}
    persistence_root = Path("weatherbot/persistence")

    for path in persistence_root.glob("*.py"):
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
