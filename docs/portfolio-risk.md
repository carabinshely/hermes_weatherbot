# Durable portfolio-risk simulation controls

Issue #16 provides backend-neutral portfolio-risk primitives retained for **internal PAPER
strategy experiments**. They do not govern public `HermesSignal` generation, customer
money, or any live-execution product path.

```text
hypothetical sizing + frozen executable quote
                ↓
internal PAPER portfolio-risk decision
                ↓
simulated BUY reservation / fill lifecycle
```

## Exposure convention

Entry exposure is exact simulated cash at risk, not mark-to-market value. Filled PAPER
positions contribute cost basis; active simulated BUYs contribute remaining reserved cash.
Liquidation marks are used for hypothetical unrealized P&L/equity, not exposure caps.

## Durable risk scope

Each simulated position key `(market_id, outcome_id)` can be assigned an immutable
`RiskScope` containing event identity, city identity, market date, and optional explicit
correlation groups. Duplicate/conflicting scope identity fails closed.

Automatic experiment correlation groups include same-event and same-date exposure;
reviewed manifests may add explicit groups such as a stable weather-system identity.

## Experiment policy

`PortfolioRiskPolicy` can test limits for total/event/city-date/correlation exposure,
open-position count, daily loss, drawdown, valuation age, clock tolerance, and loss-day
timezone.

The policy is an experiment parameter. Changing it can change hypothetical PAPER
economics but **cannot** change the public producer decision already computed by
`evaluate_candidate()`.

## Valuation and loss convention

PAPER valuation is conservative mark-to-liquidation evidence for frozen/read-only market
data. Daily P&L and drawdown are simulated research metrics only; they are not PIP track
record, customer portfolio performance, or verified profitability.

## Atomicity and deterministic replay

The retained SQLite risk/event-store primitives use transactional reservation and replay
checks to prevent duplicate hypothetical exposure during concurrent/internal simulations.
Those guarantees are useful research infrastructure and regression evidence from #16/#27.
They are not requirements for a future live backend; Hermes has no forward real-money
execution product direction.
