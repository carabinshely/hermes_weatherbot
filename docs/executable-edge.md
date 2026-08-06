# Freshness-aware executable edge

Every research signal, paper candidate, and future live order must be based on the same
`ValidatedExecutableQuote` contract.

## Point-in-time inputs

A quote evaluation combines:

- the daily-high forecast issue timestamp;
- the market-event retrieval timestamp and optional provider update timestamp;
- the selected outcome-token order-book timestamp and hash;
- an optional balance snapshot with its observation timestamp;
- the model probability and requested cash budget;
- one immutable freshness and cost policy.

A timestamp later than evaluation time beyond the configured tolerance fails closed.
Forecast, event, order-book, and balance ages are compared with independent maximum-age
limits. Every accepted quote exports the observed time, age, maximum age, and pass result
for each supplied datum.

## Executable cost

BUY quotes consume displayed asks in price order. The quote records:

- shares and displayed order-book cost;
- average, best, and worst executable prices;
- average and worst slippage from the best ask;
- platform fee reserve;
- fixed transaction-cost reserve;
- configured safety-margin reserve;
- total all-in cost and all-in average price.

The binary contract pays one unit per winning share. Therefore:

```text
expected payout  = model probability × shares
expected profit  = expected payout − total all-in cost
expected return  = expected profit / total all-in cost
probability edge = model probability − all-in average price
```

Acceptance requires positive executable edge, positive expected profit, an expected
return at or above the configured floor, and all price/slippage limits to pass.

## Thin books

The depth policy is explicit:

- `reject` rejects when the requested cash budget exceeds displayed ask notional;
- `reduce` deterministically caps the executable budget at displayed ask notional.

A reduced quote records both the requested and executable budgets. It can never exceed
the displayed book depth or the requested budget.

## Revalidation

`revalidate_executable_buy()` applies the same contract to a refreshed order book
immediately before adapter execution. A token mismatch, stale refresh, adverse price
movement, or fee-induced loss rejects the order. Paper and future live adapters must
consume this validated object rather than reconstructing price or cost from primitives.
