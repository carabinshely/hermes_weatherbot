# Hermes WeatherBot

Hermes is a **non-executing calibrated weather prediction-market signal producer**.
It combines point-in-time weather evidence with read-only Polymarket market evidence,
computes an approved calibrated probability, applies one versioned producer policy, and
emits a stable typed `HermesSignal` with auditable calibration and market-reference
provenance.

Hermes does **not** submit, cancel, redeem, sign, or manage real-money trades as a public
product capability.

## Architecture

```text
PUBLIC PRODUCER
weather + read-only market evidence
        ↓
CalibratedProbability
        ↓
versioned producer policy
        ↓
HermesSignal v1 + provenance
        ↓
local JSONL output
        ↓
optional future #54 PIP adapter

INTERNAL STRATEGY R&D
same calibrated/read-only evidence
        ↓
PAPER simulation
        ↓
hypothetical fills / sizing / portfolio state / P&L
```

`PAPER` is retained only as an internal Research and Development (R&D) harness. Its
bankroll, positions, ledger, fills, and Profit and Loss (P&L) cannot affect the public
signal identity or payload. The PAPER command/runtime also has no import path to legacy
wallet, approval, signing, or exchange-write code.

## Current calibration activation state

The calibrated runtime remains fail-closed until the pre-registered V3 evidence gate is
completed and an artifact is explicitly accepted and pinned.

```text
V3 policy                     = frozen / implemented
calibrated probability core   = implemented
accepted V3 artifact          = absent
exact-run activation evidence = pending
accepted-model signals        = fail closed
```

Do not infer acceptance from the existence of the producer runtime. Issue #49 owns the
untouched holdout evaluation; #50 owns accepted artifact/evidence pinning.

## Install

Hermes' public producer uses the minimal locked dependency profile. No wallet, Web3,
transaction-signing, or authenticated exchange package is required.

```bash
git clone https://github.com/carabinshely/hermes_weatherbot.git
cd hermes_weatherbot
uv sync --locked --no-dev
```

For development and tests:

```bash
uv sync --locked --all-groups
```

Historical live-only optional dependencies remain quarantined for compatibility and are
not part of the supported public product surface.

## Public producer CLI

The Command-Line Interface (CLI) is signal-oriented and has no execution mode selector:

```bash
uv run --no-dev python bot_v3.py scan
uv run --no-dev python bot_v3.py status
uv run --no-dev python bot_v3.py run
```

Public commands do not accept `--mode`, `--confirm-live`, `cancel`, `paper-reset`, wallet
configuration, or trading-ledger commands.

### Signal output

Accepted signals are appended to:

```text
state/signals-v1.jsonl
```

Each line is one typed `HermesSignal v1`. The logical `signal_id` is derived from:

- producer and strategy identity/version;
- versioned producer-policy fingerprint;
- venue, event, market, condition, outcome, and token identity;
- accepted decision classification;
- target market date;
- calibrated probability identity and model/artifact provenance;
- exact weather fingerprint;
- decision order-book hash and observation time;
- fixed read-only market-reference notional;
- stable executable market-reference economics such as bid/ask, all-in price, edge, and expected return.

Local processing timestamps, wallet state, PAPER state, learning history, Telegram state,
and future PIP availability are excluded from logical signal identity. The historical
quote fingerprint is audit metadata rather than logical identity because it includes its
local evaluation timestamp.

## Producer policy

Public decision-affecting configuration lives in:

```text
config/producer.json
```

Important fields include:

- `strategy_id` and `strategy_version`;
- volume and time-to-resolution filters;
- forecast/event/order-book freshness limits and future-timestamp tolerance;
- fixed `market_reference_notional`;
- cost/slippage reserves;
- minimum expected return;
- signal log path.

The reference notional is a **read-only liquidity and executable-price probe**. It is not
a bet size, customer position recommendation, or execution instruction. Public producer
schema v1 uses reject-on-insufficient-depth semantics so the reference probe is never
silently reduced to a different notional.

Changing decision-affecting policy changes `policy_fingerprint` and should be accompanied
by an explicit strategy-version change.

## Internal PAPER strategy R&D

PAPER is intentionally separate from the public CLI and has one supported command:
deterministic evaluation of a frozen experiment.

```bash
uv run --no-dev python -m weatherbot.paper evaluate \
  --manifest path/to/experiment.json \
  --output state/paper-experiments
```

The manifest is intentionally small. It names a reviewed repository-owned factory under
`weatherbot.paper.experiments.*` plus explicit arguments. Arbitrary import paths are
rejected. The factory returns a `PaperExperimentSpec` containing:

- a candidate `ProducerPolicy` with explicit strategy identity/version;
- frozen `CalibratedMarketCandidate` evidence, which is the same typed boundary consumed
  by the public producer;
- optional frozen execution order-book evidence;
- optional simulated sizing, cost, bankroll, and portfolio-risk policy;
- optional frozen settlement payout evidence for hypothetical realized P&L.

For every case, PAPER first calls the exact public
`weatherbot.producer.service.evaluate_candidate()` function. **All public producer
decisions for the experiment are computed before any simulated ledger is allocated.**
Only afterward may PAPER evaluate hypothetical sizing/risk/fills and settlement in an
isolated temporary ledger. Changing PAPER bankroll, positions, fills, or prior PAPER
outcomes therefore cannot change the real `HermesSignal` decision or payload.

Experiment identity binds the producer-policy fingerprint, strategy version, frozen
evidence fingerprints, PAPER engine version, and optional economic policy. Canonical
`summary.json`, `evaluations.jsonl`, and `checksums.json` artifacts can therefore be
reproduced byte-for-byte for the same experiment.

PAPER outputs explicitly mark fills, settlement, and Profit and Loss (P&L) as
**hypothetical development evidence**. They are not verified profitability, cannot
self-promote a strategy/model, do not grant public or paid eligibility, and are never
automatically published to the Prediction Intelligence Platform (PIP).

The older PAPER ledger/execution/valuation modules remain reusable implementation
primitives and regression evidence from #15/#16/#27. They are no longer exposed as
supported mutable `scan`, `run`, `status`, `resolve`, or `reset` PAPER command modes.

## Probability and provenance

Hermes uses the fail-closed calibrated residual model built under #12/#47/#48. A public
signal carries at least:

- `model_probability`;
- `model_version`;
- calibration artifact SHA-256 digest;
- climate region and calibrated lead day;
- forecast source and exact weather fingerprint;
- calibration group/fallback/distribution identity;
- calibration sample count and training cutoff;
- read-only executable market reference and evidence hashes.

`SHA-256` means Secure Hash Algorithm 256-bit.

There is no public fallback to the historical global fixed `2°F` uncertainty path.

## Non-execution security boundary

Continuous Integration (CI) runs `scripts/ci/check_public_non_execution.py` over both
supported runtime surfaces.

The public import graph, rooted at `bot_v3.py` and `weatherbot.producer`, must not reach:

- `bot_v3_legacy` / quarantined implementation;
- `execution_modes`;
- `weatherbot.paper`;
- wallet/live dependency helpers;
- authenticated Polymarket trading modules;
- Web3 or signing packages.

The internal PAPER CLI has a separate transitive guard that permits PAPER simulation
modules but forbids legacy execution modules, wallet/live dependency helpers,
authenticated trading clients, Web3, and signing packages.

The historical implementation remains quarantined only for historical compatibility and
regression evidence while #59 simplifies the research engine. Neither the supported public
producer nor the supported PAPER runtime imports it.

## Relationship to PIP

Issue #54 will map `HermesSignal` into PIP `SignalEnvelope v1`, sign it with a dedicated
non-wallet producer identity, and deliver it through a durable outbox.

PIP integration must not change strategy/model output. PIP unavailability cannot change
whether Hermes generated a signal.

## Product boundary

Supported public capability:

```text
weather evidence
calibrated probability
versioned edge policy
stable signal identity
read-only market reference
provenance
local output
future one-way PIP publication
```

Not a supported public capability:

```text
wallet management
private-key handling
exchange writes
order submission/cancellation/redemption
customer bankroll management
real-money position sizing
copy trading
automatic execution
PAPER P&L publication as real signals
```

## License

MIT. See `LICENSE`.
