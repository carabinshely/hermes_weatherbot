# Authoritative resolution worker

The resolution worker settles open durable-ledger positions from Polymarket's final UMA
payout vector. It never infers settlement from an order-book price, last trade, or a
locally predicted temperature.

## Required context

Every filled order intent must have write-once decision metadata containing:

```json
{
  "condition_id": "0x...",
  "market_date": "2026-04-18",
  "market_timezone": "America/Chicago",
  "bucket_key": "F:62:63",
  "declared_resolution_source": "https://..."
}
```

The worker validates this context against the current Gamma market payload before it
accepts a final payout. The market ID, condition ID, temperature bucket, local market
date, timezone, and declared source must remain consistent with the signal-time record.

## Poll states

- `pending` — the market is still open;
- `delayed` — its end time passed beyond the configured grace period without a final payout;
- `disputed` — UMA reports a dispute;
- `unavailable` — the public source could not be reached;
- `malformed` — identifiers, dates, source, payout, or payload structure are inconsistent;
- `final` — a verified binary `1/0` payout vector;
- `void` — a final `0.5/0.5` payout vector.

Only `final` and `void` produce ledger events. The worker atomically appends:

1. `MarketResolutionEvidenceRecorded` with the source URLs, retrieval and finalization
timestamps, identifiers, local market context, canonical payout value, and source-payload
SHA-256 hash;
2. `MarketResolved` with outcome-token payouts;
3. one `PositionSettled` event for each open position in the market.

Any invalid position, unknown token, conflicting evidence, or settlement invariant rolls
back the complete transaction.

## Idempotency and restart

Event IDs derive from the market, source payload hash, outcome token, and payout. A
repeated poll or restart cannot duplicate evidence, resolution, cash proceeds, or
learning records. State is rebuilt from the append-only ledger.

## Learning eligibility

Only evidence with a verified `1/0` payout is eligible for model updates. Pending,
delayed, disputed, unavailable, malformed, and void outcomes are excluded.

## Running

One cycle:

```bash
python -m weatherbot.resolution --database state/ledger.sqlite3 --once
```

Continuous monitoring:

```bash
python -m weatherbot.resolution --database state/ledger.sqlite3 --interval 600
```

`bot_v3.py run` also performs a resolution cycle during each monitor interval. The
resolution path uses only public data and the local ledger; it does not initialize a
wallet or authenticated trading client.
