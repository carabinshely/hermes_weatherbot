# Authoritative internal resolution and observation tooling

Hermes retains resolution/observation infrastructure for internal PAPER ledger regression,
research, and calibration evidence. It is **not** the public authority for the real emitted-
signal track record.

Prediction Intelligence Platform (PIP) independently preserves, resolves, and scores real
public `HermesSignal` history after receipt.

```text
Hermes internal resolution / observation tooling
        ≠
PIP independent resolution / scoring of real emitted signals
```

## Internal ledger resolution

For retained simulated/historical financial-ledger positions, the resolver accepts only a
final authoritative market payout and preserves market/condition/outcome identity, local
market date/timezone, declared resolution source, retrieval/finalization times, and source
payload hashes. It never infers settlement from a forecast, order-book price, or last
trade.

Only final/void evidence can produce internal simulated settlement events. Pending,
delayed, disputed, unavailable, or malformed evidence remains nonterminal/fail-closed.

## Exact observed weather

Exact weather observations are stored separately from market payout evidence. Observation
records preserve temperature, unit, station, measurement basis, source URL, local date and
timezone, retrieval/source timestamps, revision identity, and captured-source SHA-256.

This observation history remains relevant to calibration/research because Hermes must fit
its own model against reproducible weather truth. It does not replace PIP's independent
resolution/scoring of real emitted signals.

## Running internal tooling

Standalone resolution/observation commands remain available for internal maintenance and
research where a retained ledger requires them. They are not part of the public
`bot_v3.py scan/run/status` producer workflow.

The public producer has no financial position-settlement loop and requires no wallet or
exchange-write credentials.
