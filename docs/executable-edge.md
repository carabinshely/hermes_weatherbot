# Freshness-aware executable market reference

Hermes uses executable order-book economics as a **read-only market-reference contract**
for public signal decisions and, when frozen into an experiment, as hypothetical execution
evidence for internal PAPER Research and Development (R&D).

This contract does not authorize or imply real order submission.

## Point-in-time inputs

A quote evaluation combines the daily-high forecast issue timestamp, market-event
retrieval/update timestamps, selected outcome-token order-book timestamp/hash, model
probability, a fixed reference notional, and one immutable freshness/cost policy.

A timestamp later than evaluation time beyond the configured tolerance fails closed.
Forecast, event and order-book ages are checked independently.

## Executable cost and signal edge

BUY-side reference quotes consume displayed asks in price order and record shares,
displayed-book cost, average/best/worst executable prices, slippage, fee reserve, fixed
transaction reserve, safety margin, total all-in cost, and all-in average price.

For a binary contract paying one unit per winning share:

```text
expected payout  = model probability × shares
expected profit  = expected payout − total all-in cost
expected return  = expected profit / total all-in cost
probability edge = model probability − all-in average price
```

The public producer uses a fixed `market_reference_notional` only to probe executable
liquidity/economics. It is **not** a bet size, customer position recommendation, or order
instruction. Public producer schema v1 rejects insufficient depth rather than silently
reducing the reference notional.

## Policy provenance

Accepted reference evidence stores the complete decision-affecting freshness/cost policy.
Those values contribute to stable producer policy and evidence identity so a caller cannot
silently loosen a limit after the decision.

## PAPER reuse

Internal PAPER experiments may consume frozen execution-order-book evidence and reuse the
same quote/depth/slippage/fee primitives to estimate hypothetical fills. That simulation is
development evidence only. It does not create exchange writes, wallet actions, or verified
profitability.

Historical revalidation/submission helpers retained elsewhere in the repository are
quarantined compatibility/regression infrastructure, not a supported Hermes product path.
