# Durable event ledger

The authoritative financial state is an append-only SQLite event ledger implemented by
`weatherbot.persistence.SQLiteEventStore`. Cash, reserved cash, orders, positions, fees,
and profit/loss are rebuilt by replaying immutable `weatherbot.domain` events.

Derived balances are never written as independently mutable truth.

## Database guarantees

The store uses:

- `BEGIN IMMEDIATE` transactions for all writes;
- SQLite WAL mode and `synchronous=FULL`;
- unique event, decision, and order-intent constraints;
- deterministic JSON payloads;
- a SHA-256 payload hash and chained ledger hash for every event;
- checksummed, sequential schema migrations;
- verified replay before and after writes;
- optional state checkpoints that are validated against replay;
- SQLite's online backup API followed by integrity and replay verification.

A failed event in a multi-event append rolls back the whole transaction.

## Safe startup

```python
from weatherbot.persistence import SQLiteEventStore

with SQLiteEventStore("state/ledger.sqlite3") as store:
    recovery = store.recover()
```

`recovery.pending_orders` separates:

- `resume_submission`: an intent exists, but no backend submission fact was recorded;
- `reconcile_backend`: submission may have reached a backend and must be queried;
- terminal orders, which require no startup action.

Claimed but uncommitted scan decisions appear in `recovery.pending_decisions`.

Before calling an adapter's `submit` method, durably record its backend assignment:

```python
store.set_adapter_metadata(
    intent_id,
    backend_name="paper",
    payload={"submission_key": stable_submission_key},
)
```

This write is the submission-start marker. If the process dies after the backend accepts an order
but before `OrderSubmitted` is appended, replay still shows `created`; the marker makes startup
reconcile the backend instead of submitting the order again. A created order without the marker is
safe to resume because no backend side effect may have started yet.

Backend reconciliation remains adapter-neutral:

```python
recovery = store.reconcile_startup(resolve_adapter)
```

The resolver receives the recorded backend name. The adapter emits immutable domain events;
it does not modify balances or positions directly. Missing or mismatched adapter metadata fails
closed.

## Decision and intent idempotency

A scanner should either claim a decision before expensive work or atomically commit the resulting
intent:

```python
claim = store.claim_decision(
    decision_key,
    owner_id=worker_id,
    metadata={"scan_id": scan_id},
)

store.commit_order_intent(
    intent_created_event,
    owner_id=worker_id,
    metadata={"scan_id": scan_id},
)
```

`commit_order_intent` writes the decision record and event in one transaction. Competing workers
cannot create two intents for the same decision.

## Checkpoints

```python
checkpoint = store.create_checkpoint()
```

A checkpoint records only the event sequence, chain hash, and derived-state hash. It is a verified
integrity marker, not an alternative source of financial truth.

## Backup and restore

```python
store.backup_to("backups/ledger-2026-08-06.sqlite3")
```

Backups are written to a temporary file, verified with SQLite integrity checking, atomically
renamed, and then reopened in read-only mode for full ledger replay verification.

Restore into a closed or new destination:

```python
from weatherbot.persistence import restore_backup

restore_backup(
    "backups/ledger-2026-08-06.sqlite3",
    "state/ledger.sqlite3",
)
```

The source backup and temporary restored database are both verified before replacement.

## Corruption behavior

On startup, the store fails closed when it finds:

- SQLite structural corruption;
- missing or reordered event sequences;
- payload-hash or chain-hash mismatches;
- noncanonical or unsupported event payloads;
- indexed columns that disagree with their payload;
- invalid domain transitions during replay;
- migration gaps or checksum changes;
- checkpoints that disagree with replayed state.

The raised exception includes the failing event sequence or schema version where possible. Do not
edit ledger rows manually. Restore a verified backup or repair through an explicit, reviewed
migration.
