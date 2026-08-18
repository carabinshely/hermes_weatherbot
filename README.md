# Hermes WeatherBot

Hermes is a **non-executing calibrated weather prediction-market signal producer**.
It combines point-in-time weather evidence with read-only Polymarket market evidence,
computes a model probability, applies one versioned producer policy, and emits a stable
`HermesSignal v1` with auditable model, weather, market-reference, and policy provenance.

Hermes does **not** submit, cancel, redeem, or manage real-money trades as a supported
product capability.

## Product architecture

```text
PUBLIC PRODUCT
weather + read-only market evidence
        ↓
calibrated model probability
        ↓
accepted/versioned producer policy
        ↓
HermesSignal v1
        ↓
signed SignalEnvelope v1
        ↓
durable PIP outbox
        ↓
Prediction Intelligence Platform (PIP)
        ↓
independent receipt / history / verification / resolution / scoring / publication

INTERNAL STRATEGY R&D
frozen historical/read-only evidence
        ↓
candidate strategy
        ↓
exact public evaluate_candidate() authority
        ↓
isolated PAPER simulation
        ↓
hypothetical fills / sizing / risk / P&L
        ↓
development evidence
        ↓
explicit reviewed strategy-promotion decision
```

Hermes owns weather and market inputs, calibrated model probability, producer strategy,
provenance, signal generation, producer signing, and durable outbound publication. PIP
owns independent receipt, prospective signal history, verification, resolution, scoring,
publication, audience access, and monetization.

PIP availability and delivery state cannot change whether Hermes generated a signal.
PAPER bankroll, positions, fills, settlement, and Profit and Loss (P&L) cannot change a
real `HermesSignal` or its `SignalEnvelope`, and PAPER results are never automatically
published to PIP.

## Evidence vocabulary

| Evidence | Meaning | Does not prove |
|---|---|---|
| Model probability | Hermes estimate of outcome probability | Profitability |
| Signal edge | Model probability relative to a read-only executable market reference | Actual trade return |
| PAPER result | Hypothetical strategy-development evidence | Real execution or verified profitability |
| Emitted `HermesSignal` | Real prospective producer decision | Correctness by itself |
| PIP track record | Independently preserved, resolved, and scored emitted-signal history | PAPER trading performance |

## Current calibration activation state

The V3 calibration policy is frozen and implemented, but an accepted V3 artifact does not
exist yet. Accepted-model public signal generation therefore remains fail-closed.

```text
V3 policy                     = frozen / implemented
calibrated probability core   = implemented
accepted V3 artifact          = absent
exact-run activation evidence = pending
accepted-model signals        = fail closed
```

Issue #49 owns the pre-registered untouched holdout evaluation and must not execute before
2026-08-26. Issue #50 owns the explicit accept/reject decision and, only if accepted,
artifact/approval/evidence pinning. The existence of the producer or PIP publication
runtime must not be interpreted as calibration acceptance.

## Install

The supported public producer and internal PAPER research surfaces use the minimal locked
profile and require no wallet, Web3, transaction-signing, or authenticated exchange
package.

```bash
git clone https://github.com/carabinshely/hermes_weatherbot.git
cd hermes_weatherbot
uv sync --locked --no-dev
```

For development and tests:

```bash
uv sync --locked --all-groups
```

Historical live-only dependencies remain quarantined for compatibility and regression
coverage. Installing them does not create a supported execution mode.

## Public producer CLI

The public Command-Line Interface (CLI) is signal-oriented and has no execution-mode
selector:

```bash
uv run --no-dev python -m weatherbot.producer scan
uv run --no-dev python -m weatherbot.producer status
uv run --no-dev python -m weatherbot.producer run
```

`bot_v3.py` remains a compatibility entrypoint. For foreground process lifecycle,
service/container operation, shutdown behavior, and network/proxy policy, see
[`docs/producer-operations.md`](docs/producer-operations.md).

Public commands do not require wallet credentials and do not expose live order submission,
cancellation, redemption, approval, bankroll, or trading-ledger controls.

Accepted signals are appended to the durable JSON Lines (JSONL) signal log configured by
the producer policy. Logical signal identity binds producer/strategy identity, policy
fingerprint, market/outcome identity, target date, calibrated model provenance, weather
fingerprint, and stable read-only executable-market-reference economics. PAPER state,
wallet state, processing timestamps, and PIP availability are excluded from logical signal
identity.

## Producer policy and market reference

Decision-affecting public configuration lives in `config/producer.json`. The market-
reference notional is a **read-only liquidity and executable-price probe**. It is not a bet
size, customer recommendation, or execution instruction. Public producer schema v1 rejects
insufficient depth rather than silently changing the configured reference notional.

See `docs/executable-edge.md` for the read-only executable-reference contract.

## Internal PAPER strategy R&D

PAPER is a supported internal Research and Development (R&D) engine, not a public trading
mode and not a path to real-money execution.

```bash
uv run --no-dev python -m weatherbot.paper evaluate \
  --manifest path/to/experiment.json \
  --output state/paper-experiments
```

Experiments use frozen evidence and a reviewed repository-owned factory. PAPER first calls
the exact public `weatherbot.producer.service.evaluate_candidate()` authority for every
case. Only after the complete public decision batch exists may isolated hypothetical
sizing, portfolio-risk, fills, or settlement be evaluated.

Canonical `summary.json`, `evaluations.jsonl`, and `checksums.json` outputs are reproducible
for the same experiment identity. They explicitly mark simulated fills, settlement, and
P&L as **hypothetical development evidence**. PAPER cannot self-promote a model or strategy,
grants no public/paid eligibility, and never emits PIP lifecycle events.

See `docs/paper-trading.md` for the full internal experiment contract.

## PIP publication

Hermes can publish already-made real public `HermesSignal v1` decisions to PIP:

```text
HermesSignal v1
  -> frozen signed intent
  -> durable Hermes signal record
  -> durable PIP SQLite outbox
  -> idempotent POST /v1/events
  -> event-bound PIP receipt
```

The PIP signing key is a dedicated **Ed25519 application-identity credential**. It has no
wallet, exchange-write, transaction-signing, or funds-control authority and must never be
reused as a wallet key.

PIP delivery runs behind the separate `weatherbot.pip` worker boundary:

```bash
python -m weatherbot.pip status
python -m weatherbot.pip reconcile
python -m weatherbot.pip deliver-once
python -m weatherbot.pip run
python -m weatherbot.pip retry-dead-letter --event-id <event-id> --operator <id> --reason <reason>
python -m weatherbot.pip dead-letter --event-id <event-id> --operator <id> --reason <reason>
```

PIP independently preserves, resolves, and scores the real emitted-signal history. Hermes
PAPER trades/P&L and the historical financial ledger are not that public track record.

See `docs/pip-publication.md` for signing, durable outbox, retry, receipt, dead-letter, and
conformance details.

## Probability and provenance

Hermes uses the fail-closed calibrated residual model built under #12/#47/#48. A model-
backed signal carries the model version, calibration artifact SHA-256 digest, forecast
source and weather identity, selected calibration group/fallback/distribution, sample
count, training cutoff, and `model_probability`.

There is no public fallback to the historical fixed `2°F` uncertainty path. Calibration
validation is evidence about probabilistic quality; it is not a profitability claim.

## Non-execution security boundary

Continuous Integration (CI) enforces separate transitive import-graph guards for the
public producer and internal PAPER runtime. Supported surfaces must not reach quarantined
legacy execution code, wallet/live dependency helpers, authenticated trading clients,
Web3, exchange-write signing, approvals, cancellation, or redemption.

Historical execution code and optional live dependencies are retained only for quarantine,
compatibility, and regression purposes. They are not a supported Hermes product direction.

## Durable-state boundaries

```text
Hermes signal JSONL             = real producer signal record
PIP SQLite outbox               = downstream delivery state
PAPER/historical event ledger   = internal simulated/historical economics
```

Do not treat the internal financial/event ledger as the PIP track record and do not treat
PAPER settlement as independent scoring of real emitted signals.

## Product boundary

Supported public capability:

```text
weather evidence
calibrated model probability
versioned signal policy
stable HermesSignal identity
read-only market reference
model/input/policy provenance
signed one-way PIP publication
```

Not a supported public capability:

```text
wallet management
exchange writes
order submission/cancellation/redemption
customer bankroll management
real-money position sizing
copy trading
automatic execution
PAPER P&L publication as real history
```

## License

MIT. See `LICENSE`.
