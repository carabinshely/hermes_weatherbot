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

The approved budget covers the entire decision—not merely order-book notional. The
evaluator reserves fixed and percentage costs first, quotes only the remaining book
budget, and rejects any result whose all-in cost exceeds the approved amount. Decimal
division may leave a negligible unused remainder, but the quote cannot overspend.

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

- `reject` rejects when the available book cannot satisfy the cost-adjusted book budget;
- `reduce` deterministically caps book notional at displayed ask depth.

A reduced quote records the approved all-in budget, cost-adjusted book limit, and actual
book notional. It can never exceed displayed depth or the approved all-in budget.

## Policy provenance

An accepted quote stores the complete policy that produced it: every freshness limit,
future-timestamp tolerance, fee rate, fixed transaction reserve, safety-margin rate,
slippage limit, maximum all-in price, minimum expected return, and depth policy. These
values participate in the quote fingerprint and are exported with the decision metadata.

Revalidation fails closed if the event ID, model probability, approved budget, freshness
policy, or cost policy differs from the original decision. This prevents a caller from
loosening a limit between signal generation and execution.

## Revalidation and execution

`revalidate_executable_buy()` applies the recorded contract to a refreshed order book
and, for live mode, a refreshed balance immediately before adapter execution. A token
mismatch, stale refresh, adverse price movement, policy drift, or fee-induced loss rejects
the order.

The live boundary receives the validated quote object itself. The submitted BUY amount
is its exact displayed-book notional; the worst executable price is only a limit and is
never multiplied by shares to enlarge the order. Paper and future live adapters must
consume this object rather than reconstructing price, cost, or policy from primitives.
