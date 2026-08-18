"""Crash-safe SQLite outbox implementing PIP producer-delivery v1 mechanics."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from weatherbot.pip.core import FrozenEnvelope, PipExportError

DELIVERY_HORIZON = timedelta(days=7)
MAX_LEASE = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class OutboxItem:
    outbox_id: str
    producer_id: str
    event_id: str
    signal_id: str
    event_sha256: str
    envelope_bytes: bytes
    generated_at: datetime
    key_id: str
    state: str
    enqueued_at: datetime
    attempt_count: int
    next_attempt_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    claim_token: str | None
    claim_mode: str | None


@dataclass(frozen=True, slots=True)
class OutboxSummary:
    pending: int
    retry_wait: int
    in_flight: int
    acknowledged: int
    dead_letter: int
    oldest_unacknowledged_at: datetime | None


def _ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outbox timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _optional_ts(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PipExportError("outbox timestamp column is corrupt")
    return _parse_ts(value)


class PipOutbox:
    """Producer-owned durable outbox; frozen event bytes are never reconstructed for retry."""

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
        self._configure(timeout_seconds)
        self._migrate()
        self.verify_integrity()

    def _configure(self, timeout_seconds: float) -> None:
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(f"PRAGMA busy_timeout = {max(1, int(timeout_seconds * 1000))}")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pip_outbox (
                outbox_id TEXT PRIMARY KEY,
                producer_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                event_sha256 TEXT NOT NULL,
                envelope_bytes BLOB NOT NULL,
                generated_at TEXT NOT NULL,
                key_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'pending','in_flight','retry_wait','acknowledged','dead_letter'
                )),
                enqueued_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                next_attempt_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                claim_token TEXT,
                claim_mode TEXT CHECK(claim_mode IS NULL OR claim_mode IN (
                    'automatic','operator_one_shot'
                )),
                last_attempt_at TEXT,
                last_result_class TEXT,
                last_http_status INTEGER,
                receipt_id TEXT,
                acknowledged_at TEXT,
                dead_letter_reason TEXT,
                dead_lettered_at TEXT,
                UNIQUE(producer_id, event_id)
            );
            CREATE INDEX IF NOT EXISTS pip_outbox_due
                ON pip_outbox(state, next_attempt_at);
            CREATE TABLE IF NOT EXISTS pip_outbox_operator_audit (
                audit_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS pip_outbox_frozen_identity
            BEFORE UPDATE OF producer_id,event_id,signal_id,event_sha256,envelope_bytes,
                             generated_at,key_id,enqueued_at
            ON pip_outbox
            BEGIN
                SELECT RAISE(ABORT, 'immutable PIP outbox columns cannot be modified');
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

    def verify_integrity(self) -> None:
        rows = self._connection.execute("PRAGMA integrity_check").fetchall()
        if [str(row[0]) for row in rows] != ["ok"]:
            raise PipExportError("PIP outbox SQLite integrity check failed")

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def enqueue(self, frozen: FrozenEnvelope, *, now: datetime | None = None) -> OutboxItem:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        outbox_id = f"pob_{uuid.uuid4().hex}"
        try:
            self._begin()
            row = self._connection.execute(
                "SELECT * FROM pip_outbox WHERE producer_id=? AND event_id=?",
                (frozen.producer_id, frozen.event_id),
            ).fetchone()
            if row is not None:
                existing = self._from_row(cast(sqlite3.Row, row))
                if (
                    existing.event_sha256 != frozen.event_sha256
                    or existing.envelope_bytes != frozen.envelope_bytes
                    or existing.signal_id != frozen.signal_id
                ):
                    raise PipExportError(
                        "local PIP event identity conflict: same producer/event ID has different bytes"
                    )
                self._commit()
                return existing
            self._connection.execute(
                """
                INSERT INTO pip_outbox(
                    outbox_id,producer_id,event_id,signal_id,event_sha256,envelope_bytes,
                    generated_at,key_id,state,enqueued_at,next_attempt_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    outbox_id,
                    frozen.producer_id,
                    frozen.event_id,
                    frozen.signal_id,
                    frozen.event_sha256,
                    frozen.envelope_bytes,
                    _ts(frozen.generated_at),
                    frozen.key_id,
                    "pending",
                    _ts(current),
                    _ts(current),
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM pip_outbox WHERE outbox_id=?", (outbox_id,)
            ).fetchone()
            assert row is not None
            item = self._from_row(cast(sqlite3.Row, row))
            self._commit()
            return item
        except BaseException:
            self._rollback()
            raise

    def recover(self, *, now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            self._begin()
            expired = self._connection.execute(
                "SELECT * FROM pip_outbox WHERE state='in_flight' AND lease_expires_at<=?",
                (_ts(current),),
            ).fetchall()
            for raw in expired:
                item = self._from_row(cast(sqlite3.Row, raw))
                horizon = item.generated_at + DELIVERY_HORIZON
                if item.claim_mode == "operator_one_shot" or current >= horizon:
                    reason = (
                        "operator_one_shot_unacknowledged"
                        if item.claim_mode == "operator_one_shot"
                        else "delivery_horizon_exceeded"
                    )
                    self._connection.execute(
                        """
                        UPDATE pip_outbox SET state='dead_letter',lease_owner=NULL,
                            lease_expires_at=NULL,claim_token=NULL,claim_mode=NULL,
                            dead_letter_reason=?,dead_lettered_at=?
                        WHERE outbox_id=?
                        """,
                        (reason, _ts(current), item.outbox_id),
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE pip_outbox SET state='retry_wait',lease_owner=NULL,
                            lease_expires_at=NULL,claim_token=NULL,claim_mode=NULL,
                            next_attempt_at=?,last_result_class='expired_lease'
                        WHERE outbox_id=?
                        """,
                        (_ts(current), item.outbox_id),
                    )
            rows = self._connection.execute(
                "SELECT outbox_id,generated_at FROM pip_outbox WHERE state IN ('pending','retry_wait')"
            ).fetchall()
            for raw in rows:
                row = cast(sqlite3.Row, raw)
                generated = _parse_ts(str(row["generated_at"]))
                if current >= generated + DELIVERY_HORIZON:
                    self._connection.execute(
                        """
                        UPDATE pip_outbox SET state='dead_letter',dead_letter_reason=?,
                            dead_lettered_at=? WHERE outbox_id=?
                        """,
                        ("delivery_horizon_exceeded", _ts(current), str(row["outbox_id"])),
                    )
            self._commit()
        except BaseException:
            self._rollback()
            raise

    def claim_due(
        self,
        *,
        owner_id: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> OutboxItem | None:
        if not 1 <= lease_seconds <= int(MAX_LEASE.total_seconds()):
            raise ValueError("PIP claim lease must be between 1 and 60 seconds")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        lease_expires = current + timedelta(seconds=lease_seconds)
        minimum_generated_at = lease_expires - DELIVERY_HORIZON
        token = uuid.uuid4().hex
        try:
            self._begin()
            row = self._connection.execute(
                """
                SELECT * FROM pip_outbox
                WHERE state IN ('pending','retry_wait')
                  AND next_attempt_at<=? AND generated_at>=?
                ORDER BY next_attempt_at,enqueued_at LIMIT 1
                """,
                (_ts(current), _ts(minimum_generated_at)),
            ).fetchone()
            if row is None:
                self._commit()
                return None
            chosen = self._from_row(cast(sqlite3.Row, row))
            cursor = self._connection.execute(
                """
                UPDATE pip_outbox SET state='in_flight',attempt_count=attempt_count+1,
                    lease_owner=?,lease_expires_at=?,claim_token=?,claim_mode='automatic',
                    last_attempt_at=?
                WHERE outbox_id=? AND state IN ('pending','retry_wait')
                """,
                (owner_id, _ts(lease_expires), token, _ts(current), chosen.outbox_id),
            )
            if cursor.rowcount != 1:
                raise PipExportError("PIP outbox claim lost atomic ownership")
            row = self._connection.execute(
                "SELECT * FROM pip_outbox WHERE outbox_id=?", (chosen.outbox_id,)
            ).fetchone()
            assert row is not None
            claimed = self._from_row(cast(sqlite3.Row, row))
            self._commit()
            return claimed
        except BaseException:
            self._rollback()
            raise

    def claim_dead_letter_once(
        self,
        *,
        event_id: str,
        owner_id: str,
        operator_id: str,
        reason: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> OutboxItem:
        if not operator_id.strip() or not reason.strip():
            raise ValueError("operator identity and reason are required")
        if not 1 <= lease_seconds <= 60:
            raise ValueError("PIP claim lease must be between 1 and 60 seconds")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        token = uuid.uuid4().hex
        lease_expires = current + timedelta(seconds=lease_seconds)
        try:
            self._begin()
            row = self._connection.execute(
                "SELECT * FROM pip_outbox WHERE event_id=? AND state='dead_letter'", (event_id,)
            ).fetchone()
            if row is None:
                raise PipExportError("requested PIP dead letter does not exist")
            item = self._from_row(cast(sqlite3.Row, row))
            self._connection.execute(
                """
                INSERT INTO pip_outbox_operator_audit(
                    audit_id,event_id,operator_id,action,reason,occurred_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (uuid.uuid4().hex, event_id, operator_id, "retry_one_shot", reason, _ts(current)),
            )
            self._connection.execute(
                """
                UPDATE pip_outbox SET state='in_flight',attempt_count=attempt_count+1,
                    lease_owner=?,lease_expires_at=?,claim_token=?,claim_mode='operator_one_shot',
                    last_attempt_at=? WHERE outbox_id=? AND state='dead_letter'
                """,
                (owner_id, _ts(lease_expires), token, _ts(current), item.outbox_id),
            )
            updated = self._connection.execute(
                "SELECT * FROM pip_outbox WHERE outbox_id=?", (item.outbox_id,)
            ).fetchone()
            assert updated is not None
            claimed = self._from_row(cast(sqlite3.Row, updated))
            self._commit()
            return claimed
        except BaseException:
            self._rollback()
            raise

    def operator_dead_letter(
        self,
        *,
        event_id: str,
        operator_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        """Explicitly retain a pending/retry item as an audited dead letter."""
        if not operator_id.strip() or not reason.strip():
            raise ValueError("operator identity and reason are required")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            self._begin()
            cursor = self._connection.execute(
                """
                UPDATE pip_outbox
                SET state='dead_letter',dead_letter_reason=?,dead_lettered_at=?
                WHERE event_id=? AND state IN ('pending','retry_wait')
                """,
                (reason, _ts(current), event_id),
            )
            if cursor.rowcount != 1:
                self._commit()
                return False
            self._connection.execute(
                """
                INSERT INTO pip_outbox_operator_audit(
                    audit_id,event_id,operator_id,action,reason,occurred_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    uuid.uuid4().hex,
                    event_id,
                    operator_id,
                    "dead_letter",
                    reason,
                    _ts(current),
                ),
            )
            self._commit()
            return True
        except BaseException:
            self._rollback()
            raise

    def acknowledge(
        self,
        item: OutboxItem,
        *,
        receipt_id: str,
        result_class: str,
        http_status: int | None,
        now: datetime | None = None,
    ) -> bool:
        return self._finish(
            item,
            state="acknowledged",
            result_class=result_class,
            http_status=http_status,
            receipt_id=receipt_id,
            now=now,
        )

    def retry(
        self,
        item: OutboxItem,
        *,
        next_attempt_at: datetime,
        result_class: str,
        http_status: int | None,
        now: datetime | None = None,
    ) -> bool:
        return self._finish(
            item,
            state="retry_wait",
            result_class=result_class,
            http_status=http_status,
            next_attempt_at=next_attempt_at,
            now=now,
        )

    def dead_letter(
        self,
        item: OutboxItem,
        *,
        reason: str,
        result_class: str,
        http_status: int | None,
        now: datetime | None = None,
    ) -> bool:
        return self._finish(
            item,
            state="dead_letter",
            result_class=result_class,
            http_status=http_status,
            dead_letter_reason=reason,
            now=now,
        )

    def _finish(
        self,
        item: OutboxItem,
        *,
        state: str,
        result_class: str,
        http_status: int | None,
        now: datetime | None,
        receipt_id: str | None = None,
        next_attempt_at: datetime | None = None,
        dead_letter_reason: str | None = None,
    ) -> bool:
        if item.claim_token is None or item.lease_expires_at is None:
            raise PipExportError("cannot finish an unclaimed PIP outbox item")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if current >= item.lease_expires_at:
            return False
        if item.claim_mode == "operator_one_shot" and state == "retry_wait":
            state = "dead_letter"
            dead_letter_reason = dead_letter_reason or "operator_one_shot_unacknowledged"
        if state == "retry_wait" and next_attempt_at is None:
            raise ValueError("retry_wait requires next_attempt_at")
        cursor = self._connection.execute(
            """
            UPDATE pip_outbox SET state=?,lease_owner=NULL,lease_expires_at=NULL,
                claim_token=NULL,claim_mode=NULL,last_result_class=?,last_http_status=?,
                receipt_id=CASE WHEN ? IS NULL THEN receipt_id ELSE ? END,
                acknowledged_at=CASE WHEN ?='acknowledged' THEN ? ELSE acknowledged_at END,
                next_attempt_at=CASE WHEN ? IS NULL THEN next_attempt_at ELSE ? END,
                dead_letter_reason=CASE WHEN ? IS NULL THEN dead_letter_reason ELSE ? END,
                dead_lettered_at=CASE WHEN ?='dead_letter' THEN ? ELSE dead_lettered_at END
            WHERE outbox_id=? AND state='in_flight' AND claim_token=? AND lease_expires_at>?
            """,
            (
                state,
                result_class,
                http_status,
                receipt_id,
                receipt_id,
                state,
                _ts(current),
                _ts(next_attempt_at) if next_attempt_at is not None else None,
                _ts(next_attempt_at) if next_attempt_at is not None else None,
                dead_letter_reason,
                dead_letter_reason,
                state,
                _ts(current),
                item.outbox_id,
                item.claim_token,
                _ts(current),
            ),
        )
        return cursor.rowcount == 1

    def summary(self) -> OutboxSummary:
        counts = {
            state: 0
            for state in ("pending", "retry_wait", "in_flight", "acknowledged", "dead_letter")
        }
        for row in self._connection.execute(
            "SELECT state,COUNT(*) AS count FROM pip_outbox GROUP BY state"
        ).fetchall():
            counts[str(row["state"])] = int(row["count"])
        oldest = self._connection.execute(
            """
            SELECT MIN(enqueued_at) AS oldest FROM pip_outbox
            WHERE state!='acknowledged'
            """
        ).fetchone()
        oldest_value = None if oldest is None else oldest["oldest"]
        return OutboxSummary(
            pending=counts["pending"],
            retry_wait=counts["retry_wait"],
            in_flight=counts["in_flight"],
            acknowledged=counts["acknowledged"],
            dead_letter=counts["dead_letter"],
            oldest_unacknowledged_at=(
                _parse_ts(str(oldest_value)) if oldest_value is not None else None
            ),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> OutboxItem:
        envelope = row["envelope_bytes"]
        if not isinstance(envelope, bytes):
            raise PipExportError("outbox envelope bytes column is corrupt")
        return OutboxItem(
            outbox_id=str(row["outbox_id"]),
            producer_id=str(row["producer_id"]),
            event_id=str(row["event_id"]),
            signal_id=str(row["signal_id"]),
            event_sha256=str(row["event_sha256"]),
            envelope_bytes=envelope,
            generated_at=_parse_ts(str(row["generated_at"])),
            key_id=str(row["key_id"]),
            state=str(row["state"]),
            enqueued_at=_parse_ts(str(row["enqueued_at"])),
            attempt_count=int(row["attempt_count"]),
            next_attempt_at=_parse_ts(str(row["next_attempt_at"])),
            lease_owner=None if row["lease_owner"] is None else str(row["lease_owner"]),
            lease_expires_at=_optional_ts(row["lease_expires_at"]),
            claim_token=None if row["claim_token"] is None else str(row["claim_token"]),
            claim_mode=None if row["claim_mode"] is None else str(row["claim_mode"]),
        )
