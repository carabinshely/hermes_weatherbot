from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/persistence/codec.py",
    "from decimal import Decimal\n",
    "from decimal import Decimal, InvalidOperation\n",
)
replace_once(
    "weatherbot/persistence/codec.py",
    '''    try:
        return Decimal(text)
    except ValueError as exc:
        raise CorruptLedgerError(f"{label} is not a decimal string") from exc
''',
    '''    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise CorruptLedgerError(f"{label} is not a decimal string") from exc
    if not result.is_finite():
        raise CorruptLedgerError(f"{label} must be a finite decimal string")
    return result
''',
)

replace_once(
    "weatherbot/persistence/store.py",
    '''    ExecutionAdapter,
    LedgerEvent,
    LedgerState,
    OrderIntentCreated,
''',
    '''    ExecutionAdapter,
    FillReceived,
    LedgerEvent,
    LedgerState,
    OrderAcknowledged,
    OrderCancelled,
    OrderIntentCreated,
''',
)
replace_once(
    "weatherbot/persistence/store.py",
    '''    OrderIntentId,
    OrderState,
    apply_event,
''',
    '''    OrderIntentId,
    OrderOutcomeUnknown,
    OrderRejected,
    OrderState,
    OrderSubmitted,
    apply_event,
''',
)
replace_once(
    "weatherbot/persistence/store.py",
    '''    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        self._ensure_writable()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")
''',
    '''    @contextmanager
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
''',
)
replace_once(
    "weatherbot/persistence/store.py",
    '''    def verify_integrity(self) -> None:
        with self._lock:
            self._ensure_open()
            self._verify_sqlite_locked()
            self._load_locked()
''',
    '''    def _verify_auxiliary_locked(self) -> None:
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
            raise CorruptLedgerError(
                f"cannot read persistence metadata tables: {exc}"
            ) from exc

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
                raise CorruptLedgerError(
                    f"committed decision {claim.decision_key!r} has no intent"
                )
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
''',
)
replace_once(
    "weatherbot/persistence/store.py",
    '''            emitted = adapter.reconcile(pending.order)
            if emitted:
                self.append_many(emitted)
''',
    '''            emitted = adapter.reconcile(pending.order)
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
''',
)
