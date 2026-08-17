# PAPER strategy evaluation

PAPER is an internal strategy research and development (R&D) harness. It does not trade, does not publish producer signals, and does not establish expected live profitability.

## Architectural boundary

```text
frozen weather + market evidence
        -> calibrated probability
        -> candidate/versioned strategy
        -> StrategyDecision
             |              \
             |               -> optional hypothetical economics
             |                   sizing / portfolio risk / fill / P&L
             |                   -> development evidence only
             |
             -> accepted public strategy path is owned separately by #58
                 -> real Hermes signal
                 -> #54 SignalEnvelope / PIP
```

PIP means Prediction Intelligence Platform. P&L means profit and loss.

The invariant is strict: simulated bankroll, open positions, sizing, portfolio-risk decisions, fills, P&L, and previous PAPER outcomes may change hypothetical economic results, but they may not change the strategy-only decision for the same frozen evidence and strategy version.

## Experiment contract

`PaperExperimentSpec` contains:

- explicit strategy ID and version;
- canonical strategy parameters;
- ordered frozen `PaperEvidenceCase` inputs;
- optional experiment-only economic policy;
- the PAPER experiment engine version.

Each evidence case contains the calibrated probability object and its provenance, risk/market identity, weather snapshot, market event snapshot, decision-time order book, optional execution-time order book, optional valuation books, and a stable case ID.

The experiment ID is a SHA-256 content identity over the engine version, strategy identity/parameters, every evidence fingerprint, and the economic policy. Changing a behavior-affecting input therefore creates a different experiment ID.

## Strategy first, economics second

A caller provides a strategy evaluator with this conceptual contract:

```python
StrategyDecision evaluate_strategy(
    case: PaperEvidenceCase,
    parameters: Mapping[str, object],
)
```

The evaluator receives frozen evidence and strategy parameters only. It does not receive PAPER bankroll or portfolio state.

The resulting `StrategyDecision` records whether the candidate strategy would emit, classification/reason, the unchanged calibrated model probability, the market reference price, expected edge, and strategy metadata.

Only after `would_emit=True` may PAPER evaluate hypothetical economics. Missing execution evidence does not turn a strategy signal into a non-signal; instead the economic result is explicitly `unavailable`.

## Hypothetical economics

The initial #59 engine deliberately reuses the already-tested #15/#16/#27 machinery behind an isolated per-experiment temporary ledger:

- #15 bankroll/executable sizing;
- #16 portfolio/correlation/loss controls;
- #27 depth/slippage/fee-aware simulated execution and accounting.

The temporary ledger is created from the experiment's explicit starting cash and is destroyed at the end of the run. It never reads or writes the global PAPER ledger and therefore cannot inherit state from another run, another strategy, or the public signal producer.

This is an incremental migration seam. The exchange-shaped legacy PAPER runtime remains temporarily available for the calibration branch until #58 removes the public PAPER mode. A later #58/#59 integration step can replace the internal temporary-ledger adapter with a smaller in-memory simulator without changing the `PaperExperimentSpec` / `StrategyDecision` boundary.

## Deterministic artifacts

`write_experiment_artifacts(...)` writes canonical output under:

```text
<output>/<experiment-id>/
    summary.json
    evaluations.jsonl
    checksums.json
```

The files contain no generated wall-clock timestamp. Re-running the same experiment must produce byte-identical canonical output. Reusing the same experiment ID with conflicting content fails closed.

Signal/strategy evidence and hypothetical economic evidence remain separate in each evaluation record. The summary marks all outputs as development evidence only, not verified profitability and not public/paid eligibility.

## Safety boundary

The experiment engine has no PIP transport and no model/strategy promotion authority. It requires no wallet, private key, allowance, signing, blockchain transaction, order submission, cancellation, or redemption credential.

Frozen evidence is supplied by the caller. The experiment engine itself does not fetch a fresher market snapshot, so replay cannot silently introduce later data.

## Current stacked-branch note

Issue #58 does not yet have an implementation branch. This #59 branch is therefore based on the calibration branch and exposes a strategy-evaluator seam rather than editing the public scanner. When #58 introduces the stable public signal decision object, it should implement/adapt the same strategy-only contract and #59 should be retargeted on top of that branch before final merge.
