# PAPER trading

> **#48B integration state:** `bot_v3.py scan/run --mode paper` is implemented through the same calibrated probability boundary as RESEARCH. PAPER always recovers durable state before checking calibration availability. With no accepted artifact, or without provable exact-run forecast evidence, new model-backed PAPER decisions fail closed. LIVE strategy `scan/run` remains disabled.

PAPER mode is a deterministic **paper simulation**, not live trading and not evidence that the strategy is profitable. It exercises the production market-discovery, forecasting, calibrated-probability, pricing, sizing, portfolio-risk, accounting, recovery, and settlement contracts without a wallet, private key, signature, allowance, blockchain transaction, or Polymarket write order.

## Runtime flow

```text
recover durable PAPER runtime
        ↓
load separately accepted calibration artifact
        ↓
public market + forecast snapshots
        ↓
shared CalibratedProbabilityRuntime
        ↓
CalibratedProbability + exact provenance
        ↓
#15 bankroll sizing / #17 executable quote
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

Global calibration failure stops new strategy scanning after recovery and before weather/market collection. Candidate-local calibration failures reject only that candidate. The exact forecast-vintage boundary remains fail-closed: a forecast must satisfy the calibrated decision window and prove the required previous-day 18Z ECMWF run identity.

PAPER mode is deliberately routed **before** the legacy `kelly × MAX_BET` research-reference sizing code. PAPER sizing comes only from the durable #15 bankroll sizing policy and #16 portfolio-risk controls. `MAX_BET` is the configured per-trade ceiling inside the shared sizing policy, not a transient scanner bankroll base.

## Calibration identity and idempotency

RESEARCH and PAPER consume the same immutable `CalibratedProbability` object. It contains:

```text
model_probability
model_version
artifact_sha256
city_slug
climate_region
lead_days
forecast_source
calibration_group_key
fallback_level
distribution_type
calibration_sample_count
training_cutoff
```

The PAPER scanner decision ID hashes a canonical fingerprint of this complete calibration identity together with market/outcome identity, weather/event fingerprints, and the decision-book hash. A different artifact, probability, group/fallback, lead, or other calibrated input therefore creates a different durable decision identity even when the model-version string is unchanged.

Scanner callers cannot independently supply `model_version` or `model_probability` to the PAPER facade. They also cannot override calibration-owned audit keys. The facade derives the generic `PaperEntryRequest` model fields from the typed calibrated result and persists the canonical calibration mapping under `caller_audit.calibration`.

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

The starting balance is written exactly once as `AccountOpened`. Reopening an existing ledger with a different configured starting balance fails closed instead of silently resetting history. #48B does not migrate or reset existing PAPER history.

## Commands

Run a one-shot calibrated PAPER strategy scan:

```bash
python bot_v3.py scan --mode paper
```

Run continuous PAPER scanning plus mechanical resolution monitoring:

```bash
python bot_v3.py run --mode paper
```

These commands are **implemented but may intentionally create zero new decisions**. Before #49 accepts an artifact, global calibration loading fails closed after PAPER recovery and before weather/market scanning. After an artifact is accepted, candidate generation still requires exact compatible forecast-run provenance; the current stitched Open-Meteo path must not invent that provenance.

Show PAPER account status:

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

LIVE strategy commands remain blocked independently of whether a calibration artifact is accepted.

## Audit trail

Each PAPER decision persists JSON-safe exact audit data including:

- top-level model version and model probability derived from the typed calibrated result;
- canonical `caller_audit.calibration` with artifact SHA, city/region/lead inputs, forecast source, selected group/fallback, distribution, sample count, training cutoff, and model probability;
- forecast and observation snapshot identity/timestamps;
- market snapshot identity/timestamps;
- full decision-time and submit-time order-book levels and hashes;
- quote age;
- #15 sizing inputs/formula/caps;
- #16 portfolio-risk decision metadata;
- resolution context;
- execution-plan fingerprint and durable execution-plan payload.

Legacy floating-point caller metadata is converted to strings before persistence so it cannot silently lose precision through JSON encoding.

## Historical evidence boundary

Pre-calibration/fixed-sigma PAPER history must not be mixed with calibrated strategy-performance evidence. Existing ledgers are never automatically reset or rewritten. If an operator wants a clean experimental cohort after final #49/#48 activation, the explicit archive/reset command is the boundary.

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

PAPER mode does not auto-tune parameters. Issue #26 remains the gate for any adaptive policy changes. The untouched #49 holdout and final #12 activation evidence still gate interpretation of PAPER strategy results. No PAPER result should be described as expected live profitability.
