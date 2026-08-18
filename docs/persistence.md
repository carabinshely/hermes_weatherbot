# Internal durable financial/event ledger

The SQLite financial/event ledger implemented by `weatherbot.persistence.SQLiteEventStore`
is retained **internal simulation/historical infrastructure**. It is not Hermes' public
signal history and it is not the PIP publication outbox.

```text
Hermes signal JSONL             = real producer signal record
PIP SQLite outbox               = downstream PIP delivery state
financial/event ledger          = internal PAPER/historical economics
```

## Ledger guarantees

The ledger rebuilds cash, reservations, simulated orders, positions, fees, and P&L from
immutable domain events. Derived balances are never independently mutable truth.

The store uses transactional writes, SQLite Write-Ahead Logging (WAL), `synchronous=FULL`,
unique identity constraints, deterministic JSON payloads, SHA-256 payload/chained hashes,
checksummed schema migrations, replay verification, validated checkpoints, and verified
backup/restore.

## Research role

#15/#16/#27 PAPER primitives may reuse this ledger inside isolated deterministic
experiments when bankroll/sizing/risk/fill behavior is part of the research question.
Those simulated financial events cannot affect a public `HermesSignal`, cannot become a
`SignalEnvelope`, and are not PIP track-record evidence.

The #59 experiment engine computes the complete public decision batch before allocating an
isolated PAPER ledger. That ordering prevents simulated bankroll, positions, fills, and
prior outcomes from changing real signal decisions.

## Idempotency and recovery

Retained ledger helpers preserve deterministic event/decision identities, atomic intent
reservation, replay verification, and crash recovery. These properties remain useful for
research reproducibility and regression testing; they do not imply a supported real-money
execution backend.

## Corruption behavior

The store fails closed on structural corruption, missing/reordered events, hash mismatch,
noncanonical payloads, indexed-column disagreement, invalid domain transitions, migration
mismatch, or checkpoint disagreement. Do not edit ledger rows manually; repair only via an
explicit reviewed migration or verified backup.
