# PAPER trading

> **#48A integration state:** the PAPER simulation subsystem remains implemented, testable, and administratively accessible, but `bot_v3.py scan --mode paper` and `bot_v3.py run --mode paper` are intentionally disabled until the calibrated strategy-to-PAPER integration is reviewed. This phase does not generate new PAPER candidates from the public scanner.

PAPER mode is a deterministic **paper simulation**, not live trading and not evidence that the strategy is profitable. It exercises the production market-discovery, forecasting, pricing, sizing, portfolio-risk, accounting, recovery, and settlement contracts without a wallet, private key, signature, allowance, blockchain transaction, or Polymarket write order.

## Runtime flow

```text
public market + forecast snapshots
        ↓
#17 executable quote
        ↓
#15 bankroll sizing
        ↓
fresh bid-side portfolio valuation
        ↓
#16 atomic portfolio-risk gate
        ↓
durable BUY intent + durable paper execution plan
        ↓
second contemporaneous public order-book snapshot
        ↓
paper full fill / partial fill + cancel / rejection
        ↓
shared immutable ledger events
        ↓
#13 authoritative resolution + settlement
```

PAPER mode is deliberately routed **before** the legacy `kelly × MAX_BET` scanner code. `MAX_BET` is only the configured per-trade ceiling used by the #15 sizing policy.

## Execution assumptions

A paper submission is repriced from a second contemporaneous selected-token CLOB order book. The simulator consumes displayed depth only and applies the same configured average- and worst-price slippage limits used by executable quoting.

- A full fill requires enough acceptable displayed size for the approved quantity.
- A partial fill is allowed only when the executable quantity still satisfies the market minimum; the unavailable remainder is cancelled and its reservation is released.
- If acceptable executable quantity is below the market minimum, the simulated order is rejected.
- No fill occurs at midpoint, last trade, or a size-independent best price.
- Simulated fill fees are platform fee plus fixed transaction cost. The #17 safety margin remains a reservation buffer and is **not** booked as a fake realized fee.

The paper execution plan records the exact book hash, observed time, condition ID, token ID, requested/filled quantity, average/worst price, gross value, fee, consumed price levels, and reason. The plan is persisted in the same SQLite transaction as the approved BUY intent so restart recovery never has to guess a fill from a later market snapshot.

## Position identity and settlement

The durable position key is `(market_id, outcome_id)`, where PAPER uses the selected CLOB token ID as `outcome_id`. This matches the #13 Gamma/UMA resolver, whose payout vector is keyed by YES/NO token IDs.

Every accepted decision also stores `condition_id`, `market_date`, `market_timezone`, `bucket_key`, and the declared resolution source when available. The existing `ResolutionWorker` performs win/loss/void settlement. Settlement is idempotent across restart; PAPER does not implement a separate settlement accounting model.

## Valuation and risk

Open PAPER positions are marked at **size-aware executable bid-side liquidation value**, net of simulated exit fees. Any quantity not covered by displayed bids contributes zero liquidation value. This convention is intentionally conservative and avoids midpoint/last-trade marks.

The resulting valuation feeds the same #16 portfolio controls used by future live execution:

- duplicate open exposure;
- total exposure;
- event exposure;
- city/date exposure;
- automatic same-event and same-date correlation groups plus explicit correlation groups;
- maximum open positions;
- realized-today plus current unrealized daily-loss breaker;
- drawdown breaker.

Entry circuit breakers block new BUYs only. SELL exits, monitoring, resolution, and settlement remain available.

## Durable PAPER ledger

Default paths and limits are configured in `config.json`:

```json
{
  "paper_starting_cash": 100.0,
  "paper_ledger_path": "state/paper-ledger.sqlite3",
  "paper_archive_directory": "state/paper-archive",
  "paper_max_total_exposure": 20.0,
  "paper_max_event_exposure": 6.0,
  "paper_max_city_date_exposure": 6.0,
  "paper_max_correlation_exposure": 8.0,
  "paper_max_open_positions": 10,
  "paper_max_daily_loss": 10.0,
  "paper_max_drawdown": 20.0,
  "paper_loss_timezone": "UTC"
}
```

The starting balance is written exactly once as `AccountOpened`. Reopening an existing ledger with a different configured starting balance fails closed instead of silently resetting history.

## Commands

### Strategy scan gate during #48A

The public PAPER strategy commands are intentionally disabled in this phase:

```bash
python bot_v3.py scan --mode paper
python bot_v3.py run --mode paper
```

Both exit with status 2 before strategy/calibration/network work. They are **not** the supported way to generate PAPER candidates until the remaining #48 integration is completed. The deterministic PAPER service, fixtures, ledger, recovery, valuation, risk, and settlement tests remain available.

Administrative/mechanical PAPER commands remain supported. Show PAPER account status:

```bash
python bot_v3.py status --mode paper
```

The status includes starting cash, cash, reserved and available cash, market value, realized/unrealized P&L, fees, exposure, equity, drawdown, and open-position count. Wallet access remains disabled.

Resolve pending PAPER positions through the authoritative resolver:

```bash
python bot_v3.py resolve --mode paper
```

Reset is intentionally explicit and always archives verified history first:

```bash
python bot_v3.py paper-reset --mode paper --confirm-paper-reset
```

Without `--confirm-paper-reset`, the command exits without changing history.

## Audit trail

Each PAPER decision persists JSON-safe exact audit data including:

- model version and model probability;
- forecast and observation snapshot identity/timestamps;
- market snapshot identity/timestamps;
- full decision-time and submit-time order-book levels and hashes;
- quote age;
- #15 sizing inputs/formula/caps;
- #16 portfolio-risk decision metadata;
- resolution context;
- execution-plan fingerprint and durable execution-plan payload.

Legacy floating-point caller metadata is converted to strings before persistence so it cannot silently lose precision through JSON encoding.

## Reproducible completion evidence

Generate the deterministic #27 evidence report with:

```bash
python scripts/paper_fixture_report.py
```

or write it to a file:

```bash
python scripts/paper_fixture_report.py --output /tmp/paper-report.json
```

The report runs five independent ledgers and verifies integrity for:

1. winning settlement;
2. losing settlement;
3. void settlement;
4. execution rejection for insufficient acceptable depth;
5. depth-limited partial fill with remainder cancellation.

The dedicated PAPER test suite also includes a simulated crash between atomic intent/plan persistence and lifecycle-event append, restart recovery, duplicate-scan prevention, explicit reset/archive behavior, and an HTTP network guard proving the core paper service does not need a live write endpoint.

## Quality gate

CI runs the complete repository suite on Python 3.12 and 3.13 and separately enforces at least **70% focused branch-aware coverage** for `weatherbot.paper`. The floor is deliberately below the measured baseline so it detects meaningful regression without incentivizing low-value exception-only tests.

## Release caveats

PAPER mode does not auto-tune parameters. Issue #26 remains the gate for any adaptive policy changes. Repository-wide security/documentation/data-quality gates such as #2, #12, #24, and #25 still apply before treating PAPER results as release-quality strategy evidence. No PAPER result should be described as expected live profitability.
