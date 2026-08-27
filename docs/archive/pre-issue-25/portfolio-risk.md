# Durable portfolio risk controls

Issue #16 adds the portfolio-level permission layer that composes after bankroll sizing (#15) and before order-intent creation.

```text
#15 bankroll sizing + executable quote
                |
                v
#16 portfolio risk decision
                |
                v
PortfolioRiskEventStore.commit_risk_checked_order_intent(...)
                |
                v
 durable BUY reservation / future #27 paper adapter
```

## Exposure convention

Entry exposure is exact cash at risk, not mark-to-market value:

- each open filled position contributes its ledger `cost_basis`;
- each active non-terminal BUY contributes its remaining `reserved_cash`;
- a partially filled BUY therefore contributes filled cost basis plus its remaining reservation;
- SELL reservations do not add entry exposure.

This deliberately differs from valuation. Liquidation marks are used for unrealized PnL and equity, not for exposure caps.

## Durable risk scope

Every approved position key `(market_id, outcome_id)` is assigned one immutable `RiskScope` before its BUY intent is appended. The scope stores:

- event identity;
- city identity;
- market date;
- optional explicit correlation groups such as a weather-system identifier.

The position key deterministically defines the `RiskScopeRegistered` event ID. Reusing the same position key with different scope data is therefore a ledger conflict rather than a silent regrouping.

Correlation groups automatically include:

- `event:<event_id>` so mutually related outcomes in the same event are correlated;
- `date:<YYYY-MM-DD>` so same-date weather exposure is correlated;
- caller-supplied groups, for example `weather-system:<stable-id>`.

A legacy open position or active BUY without durable scope makes new entry evaluation fail closed. Existing exposure is never silently assigned a guessed city/event/correlation identity.

## Portfolio limits

`PortfolioRiskPolicy` is an explicit fixed policy object. It defines:

- maximum total open exposure;
- maximum exposure per event;
- maximum exposure per city/date;
- maximum exposure per correlation group;
- maximum number of exposed position keys;
- maximum daily loss;
- maximum drawdown;
- maximum valuation age and clock tolerance;
- the timezone used to define a loss day.

The policy is backend-neutral. Paper and future live adapters must receive the same policy and risk decision contract. Product-specific configuration wiring belongs at the composition root; the evaluator itself does not read JSON, environment variables, wallets, or exchange SDKs.

## Valuation and loss convention

A `PortfolioValuation` is a mark-to-liquidation snapshot for the exact currently open position quantities. It must reconcile:

```text
portfolio equity = ledger cash + sum(position liquidation values)
```

Valuation events are replay-validated against the exact financial state at their ledger sequence. Missing positions, quantity mismatches, currency mismatches, or non-reconciling equity fail closed.

Daily PnL is intentionally conservative:

```text
realized PnL today
+ current unrealized liquidation PnL
= daily PnL

daily loss = max(0, -daily PnL)
```

An unrealized loss that existed before midnight is therefore not magically reset away; it continues to contribute to the circuit breaker while it remains in the portfolio.

Drawdown is measured from the durable high-water mark:

```text
high-water mark = max(initial cash, durable historical valuation equities, current equity)
drawdown        = high-water mark - current equity
```

Valid valuation evidence is persisted even when another portfolio rule rejects the proposed entry. Stale or mismatched valuations are never persisted as trusted evidence.

## Atomicity and concurrent scans

A scanner-side check followed by a later order insert is unsafe. Two different decision IDs can both observe the same pre-trade state and exceed a portfolio cap together.

`PortfolioRiskEventStore.commit_risk_checked_order_intent(...)` therefore runs under the event store's `BEGIN IMMEDIATE` SQLite transaction and performs this sequence atomically:

```text
load current ledger + durable risk events
        |
        v
evaluate duplicate/exposure/loss/drawdown controls
        |
        +-- rejected --> complete decision claim; append no BUY intent
        |
        v
register immutable risk scope
append validated portfolio valuation
append OrderIntentCreated
reserve cash through the normal financial reducer
commit transaction
```

`PortfolioRiskEventStore.commit_order_intent(...)` fails closed for BUY intents so callers cannot accidentally bypass this transaction. SELL intents retain the ordinary durable intent path.

The concurrency regression test uses two independent SQLite connections, two different decision IDs, and a shared total-exposure cap. Only one transaction can reserve cash; the other must observe that reservation and persist a rejection.

## #27 integration contract

Paper execution (#27) must:

1. create the #15 `SizingDecision` from durable available cash and the executable quote;
2. assemble fresh liquidation marks for all open positions;
3. build the proposed `RiskScope` from the selected market/event/city/date and any known weather-system correlation group;
4. create the backend-neutral BUY `OrderIntentCreated` using the sized executable amount;
5. call `PortfolioRiskEventStore.commit_risk_checked_order_intent(...)`;
6. submit to the paper adapter only when that atomic commit returns an approved, committed decision.

No paper or future-live backend should implement its own duplicate, exposure, daily-loss, or drawdown formula.
