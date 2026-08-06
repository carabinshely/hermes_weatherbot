# Authoritative resolution worker

The resolution worker settles open durable-ledger positions from Polymarket's final UMA
payout vector. It never infers settlement from an order-book price, last trade, forecast,
or locally derived temperature label.

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

Only `final` and `void` produce settlement events. The worker atomically appends:

1. `MarketResolutionEvidenceRecorded` with the source URLs, retrieval and finalization
timestamps, identifiers, local market context, canonical payout value, and source-payload
SHA-256 hash;
2. `MarketResolved` with outcome-token payouts;
3. one `PositionSettled` event for each open position in the market.

Any invalid position, unknown token, conflicting evidence, or settlement invariant rolls
back the complete transaction.

## Exact observed weather

The final Polymarket payout and the exact observed weather value are deliberately stored
as separate immutable facts:

- settlement evidence answers which outcome-token payout became authoritative;
- weather observation evidence records the exact reported temperature, unit, station,
measurement basis, source URL, market-local date and timezone, retrieval time, source
timestamp, source revision, and captured-source SHA-256 hash;
- derived labels and learning outcomes join those facts later rather than overwriting
either one.

A captured source revision is recorded with:

```bash
python -m weatherbot.resolution.observation_cli \
  --database state/ledger.sqlite3 \
  --market-id 1996416 \
  --market-date 2026-04-18 \
  --market-timezone America/Chicago \
  --temperature 63 \
  --unit F \
  --source-name "Weather Underground daily history" \
  --source-url "https://www.wunderground.com/history/daily/us/il/chicago/KMDW" \
  --station-id KMDW \
  --measurement-basis "finalized daily high temperature" \
  --source-revision final-v1 \
  --payload-file captures/chicago-2026-04-18.json
```

A corrected source version is appended with `--status revised` and
`--supersedes-payload-hash <prior-sha256>`. The original observation remains in the
ledger. A revision cannot change the source, station, measurement basis, market date,
timezone, or unit.

## Idempotency and restart

Event IDs derive from the market, source payload hash, outcome token, and payout. A
repeated poll or restart cannot duplicate settlement evidence, cash proceeds, position
settlements, weather observations, or learning records. State is rebuilt from the
append-only ledger.

## Learning eligibility

A model update requires both:

1. verified non-void `1/0` settlement evidence; and
2. a matching final or revised exact weather observation from the declared resolution
source for the same local market date and timezone.

Pending, delayed, disputed, unavailable, malformed, void, provisional, missing-source,
and source-mismatched outcomes are excluded. This prevents a market payout, a forecast,
or an inferred bucket label from masquerading as an observed temperature.

## Running

One settlement cycle:

```bash
python -m weatherbot.resolution --database state/ledger.sqlite3 --once
```

Continuous settlement monitoring:

```bash
python -m weatherbot.resolution --database state/ledger.sqlite3 --interval 600
```

`bot_v3.py run` also performs a resolution cycle during each monitor interval. The
resolution path uses only public data and the local ledger; it does not initialize a
wallet or authenticated trading client.
