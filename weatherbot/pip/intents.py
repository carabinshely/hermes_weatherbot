"""Crash-recoverable staging for frozen PIP envelopes before Hermes signal commit."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from weatherbot.pip.core import FrozenEnvelope, PipExportError
from weatherbot.pip.outbox import PipOutbox


def _ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("PIP intent timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class PipIntentStore:
    """Retain exact frozen bytes across the signal-log/outbox crash window.

    An intent is deliberately not deliverable. The producer stages it before committing the
    Hermes JSONL signal, then promotes it only after the signal fsync succeeds. If the process
    crashes after signal fsync but before promotion, reconciliation promotes the same exact bytes
    without rebuilding or re-signing under whatever key happens to be current after restart.
    """

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=timeout_seconds,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._closed = False
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(f"PRAGMA busy_timeout = {max(1, int(timeout_seconds * 1000))}")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._migrate()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pip_publication_intent (
                signal_id TEXT PRIMARY KEY,
                producer_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_sha256 TEXT NOT NULL,
                canonical_event_bytes BLOB NOT NULL,
                envelope_bytes BLOB NOT NULL,
                generated_at TEXT NOT NULL,
                key_id TEXT NOT NULL,
                staged_at TEXT NOT NULL,
                UNIQUE(producer_id, event_id)
            );
            CREATE TRIGGER IF NOT EXISTS pip_publication_intent_immutable
            BEFORE UPDATE ON pip_publication_intent
            BEGIN
                SELECT RAISE(ABORT, 'immutable PIP publication intent cannot be modified');
            END;
            """
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._closed = True

    def stage(self, frozen: FrozenEnvelope, *, now: datetime | None = None) -> FrozenEnvelope:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        row = self._connection.execute(
            "SELECT * FROM pip_publication_intent WHERE signal_id=?",
            (frozen.signal_id,),
        ).fetchone()
        if row is not None:
            existing = self._from_row(cast(sqlite3.Row, row))
            if existing != frozen:
                raise PipExportError(
                    "PIP publication intent conflict: signal_id already has different frozen bytes"
                )
            return existing
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM pip_publication_intent WHERE signal_id=?",
                (frozen.signal_id,),
            ).fetchone()
            if row is not None:
                existing = self._from_row(cast(sqlite3.Row, row))
                if existing != frozen:
                    raise PipExportError(
                        "PIP publication intent conflict: signal_id already has different frozen bytes"
                    )
                self._connection.execute("COMMIT")
                return existing
            self._connection.execute(
                """
                INSERT INTO pip_publication_intent(
                    signal_id,producer_id,event_id,event_sha256,canonical_event_bytes,
                    envelope_bytes,generated_at,key_id,staged_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    frozen.signal_id,
                    frozen.producer_id,
                    frozen.event_id,
                    frozen.event_sha256,
                    frozen.canonical_event_bytes,
                    frozen.envelope_bytes,
                    _ts(frozen.generated_at),
                    frozen.key_id,
                    _ts(current),
                ),
            )
            self._connection.execute("COMMIT")
            return frozen
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def get(self, signal_id: str) -> FrozenEnvelope | None:
        row = self._connection.execute(
            "SELECT * FROM pip_publication_intent WHERE signal_id=?",
            (signal_id,),
        ).fetchone()
        return None if row is None else self._from_row(cast(sqlite3.Row, row))

    def discard(self, signal_id: str) -> bool:
        """Remove staging only when the caller knows the Hermes signal did not commit."""
        cursor = self._connection.execute(
            "DELETE FROM pip_publication_intent WHERE signal_id=?",
            (signal_id,),
        )
        return cursor.rowcount == 1

    def has_outbox_signal(self, signal_id: str) -> bool:
        """Return whether outbox already owns the signal and retire redundant staging.

        The cleanup handles a crash after durable outbox enqueue but before normal intent deletion.
        Once exact bytes are in the durable outbox, the staging row has no remaining recovery role.
        """
        table = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pip_outbox'"
        ).fetchone()
        if table is None:
            return False
        row = self._connection.execute(
            "SELECT 1 FROM pip_outbox WHERE signal_id=? LIMIT 1",
            (signal_id,),
        ).fetchone()
        if row is None:
            return False
        self.discard(signal_id)
        return True

    def promote(self, signal_id: str, *, now: datetime | None = None) -> bool:
        """Promote an exact staged intent into the durable outbox, then retire the intent.

        Enqueue happens before intent deletion. A crash between the two is harmless because
        outbox enqueue is idempotent and the still-present intent can be promoted again.
        """
        frozen = self.get(signal_id)
        if frozen is None:
            return False
        current = (now or datetime.now(UTC)).astimezone(UTC)
        with PipOutbox(self.path) as outbox:
            outbox.enqueue(frozen, now=current)
            outbox.recover(now=current)
        self._connection.execute(
            "DELETE FROM pip_publication_intent WHERE signal_id=? AND event_sha256=?",
            (signal_id, frozen.event_sha256),
        )
        return True

    def count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM pip_publication_intent"
        ).fetchone()
        return 0 if row is None else int(row["count"])

    @staticmethod
    def _from_row(row: sqlite3.Row) -> FrozenEnvelope:
        canonical = row["canonical_event_bytes"]
        envelope = row["envelope_bytes"]
        if not isinstance(canonical, bytes) or not isinstance(envelope, bytes):
            raise PipExportError("PIP publication intent bytes are corrupt")
        return FrozenEnvelope(
            producer_id=str(row["producer_id"]),
            event_id=str(row["event_id"]),
            signal_id=str(row["signal_id"]),
            generated_at=_parse_ts(str(row["generated_at"])),
            key_id=str(row["key_id"]),
            event_sha256=str(row["event_sha256"]),
            canonical_event_bytes=canonical,
            envelope_bytes=envelope,
        )
