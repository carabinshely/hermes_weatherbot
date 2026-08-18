# Internal PAPER strategy R&D

PAPER is Hermes' deterministic internal strategy-evaluation engine. It is not a public
trading mode, not a PIP publication surface, and not a path to real-money execution.

## Supported command

```bash
uv run --no-dev python -m weatherbot.paper evaluate \
  --manifest path/to/experiment.json \
  --output state/paper-experiments
```

The manifest names a reviewed repository-owned factory under
`weatherbot.paper.experiments.*` plus explicit arguments. Arbitrary import paths are
rejected. A factory returns a `PaperExperimentSpec` containing a candidate
`ProducerPolicy`, frozen `CalibratedMarketCandidate` evidence, and optional frozen
execution-order-book, sizing, bankroll, portfolio-risk, and settlement evidence.

## Decision/economics boundary

```text
frozen calibrated candidate + candidate ProducerPolicy
        ↓
weatherbot.producer.service.evaluate_candidate()
        ↓
HermesSignal | public rejection
        ↓
-------------------------------- strategy/economics boundary
        ↓
optional isolated PAPER sizing / risk / fill / settlement
        ↓
hypothetical development evidence only
```

The complete public decision batch is computed before any simulated ledger is allocated.
Changing PAPER bankroll, positions, fills, prior outcomes, or simulated settlement cannot
change the real `HermesSignal` decision or payload.

Hypothetical settlements are applied in event-time order. A future settlement cannot
change an earlier decision. This preserves deterministic replay without look-ahead bias.

## Experiment identity and outputs

Experiment identity binds the producer-policy fingerprint, strategy version, frozen
evidence fingerprints, running PAPER engine version, and optional economic policy.

A successful experiment writes canonical:

```text
summary.json
evaluations.jsonl
checksums.json
```

The same frozen experiment reproduces byte-identical canonical artifacts. Conflicting
reuse of an experiment identity fails closed.

## Economic simulation

PAPER may reuse the #15/#16/#27 sizing, executable-depth, portfolio-risk, ledger, fill,
and settlement primitives when they improve a strategy experiment. These are **internal
simulation primitives**, not a forward live-execution roadmap.

Simulated fills use frozen/read-only executable quote/depth/slippage/fee evidence where
provided. Simulated bankroll, sizing and portfolio-risk policy are experiment parameters
only and cannot affect public signal generation.

## Evidence status

PAPER fills, settlement and Profit and Loss (P&L) are always **hypothetical development
evidence**. They:

- are not verified profitability;
- are not real execution history;
- are not independently scored PIP history;
- cannot self-promote a model or strategy;
- do not grant public or paid eligibility;
- are never automatically exported as PIP events.

Promotion from a candidate strategy to a real signal-emitting strategy is an explicit,
reviewed, versioned decision outside the PAPER engine.

## Security boundary

PAPER requires no wallet, private key, token approval, transaction signing, exchange-write
credential, order submission, cancellation, or redemption capability. Continuous
Integration (CI) enforces a separate transitive non-execution import guard for the PAPER
runtime.

The historical mutable PAPER command surface is retired. The supported interface does not
include `bot_v3.py` PAPER `scan`, `run`, `status`, `resolve`, or `reset` modes.

## Relationship to calibration and PIP

PAPER consumes the same calibrated/model-probability semantics as the public producer but
can evaluate candidate strategies that are not export-eligible. Calibration acceptance is
separate from PAPER economics, and PAPER economics are not evidence of calibration quality.

PIP independently preserves, resolves and scores real emitted `HermesSignal` history.
PAPER trades/P&L are not part of that history.
