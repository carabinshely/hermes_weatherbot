# Forecast uncertainty calibration

Issue #12 replaces the historical fixed `2°F` uncertainty assumption with a versioned,
reproducible residual-distribution model. This document defines the scientific/runtime
boundary. Calibration evidence measures probability quality; it does **not** claim
profitability.

## Core identity

For a market-local target date:

```text
residual = finalized observed daily high - point-in-time forecast daily high
```

The model estimates a residual distribution under a deterministic conditioning/fallback
hierarchy and converts it into probabilities over the same `TemperatureBucket` boundaries
used by market interpretation.

Public/runtime terminology is **model probability** (`model_probability`), never "true
probability".

## Forecast source contract

Current calibration work is tied to Open-Meteo ECMWF IFS 0.25° daily maximum temperature,
with exact city/coordinates, market timezone/local target date, lead-day mapping, and
point-in-time/model-run provenance. Historical samples must reproduce the effective
production forecast contract closely enough to be treated as the same source. Source or
run semantics that materially differ require a separately versioned source/model contract.

No historical sample may use future information. Raw and normalized evidence retain
content hashes and explicit source/run/retrieval identity.

## Observation target contract

Calibration truth preserves the market's declared resolution measurement as closely and
reproducibly as possible: station/location, market-local date/timezone, finalized daily
high, whole-degree Fahrenheit semantics, source/revision identity, and captured-source
hash. Missing, provisional, ambiguous, mismatched, or insufficient-coverage observations
are excluded explicitly rather than imputed.

PIP independently resolves/scores real emitted signals; that downstream responsibility
does not replace Hermes' need for historical observation evidence to fit its own model.

## Frozen V3 policy

Residual conditioning falls back deterministically:

```text
city + source + lead + season
  -> climate region + source + lead + season
  -> source + lead + season
  -> source + lead
  -> source
```

V3 requires an eligible finite-variance bias-adjusted normal fit for runtime groups. Raw
unsmoothed empirical ECDF is diagnostic-only. Unsafe/sparse specific groups are omitted so
a deterministic broader fallback can be used; if no compatible level satisfies evidence
policy, probability generation rejects instead of inventing a default uncertainty.

The final V3 policy was frozen before the untouched holdout and must not be changed after
holdout outcomes are inspected.

## Time separation and final holdout

Development/model-selection evidence ends at `2026-08-10`.

```text
final untouched holdout = 2026-08-11 .. 2026-08-24
```

Issue #49 must not execute the holdout before **2026-08-26** under the finalized-history
guard. It evaluates V3 exactly once using proper probabilistic scoring, reliability,
fallback/group coverage, missingness/source-gap evidence, sample counts, and checksums.

If V3 fails materially, it is rejected and a new model-development issue must preregister
a new future holdout. The same interval cannot be tuned on and then called untouched.

## Artifact / approval contract

A runtime artifact is immutable and content-addressed. It records model/source contracts,
training/validation ranges, dataset/manifest identity, minimum-sample policy, fitted groups,
distribution parameters, and diagnostics.

Mechanical validity is not acceptance. Separate repository-controlled approval evidence
must pin an accepted artifact. Missing, malformed, rejected, corrupt, checksum-mismatched,
or source-incompatible approval/artifact state fails closed. There is no fixed-sigma
fallback.

Every model-backed public signal retains at least:

```text
model_version
artifact_sha256
forecast_source
forecast/input identity
calibration_group_key
fallback_level
distribution_type
calibration_sample_count
training_cutoff
model_probability
```

## Product/runtime boundary

The current architecture is:

```text
accepted calibrated probability semantics
        ↓
public evaluate_candidate() authority
        ├── real HermesSignal v1 decisions
        │       ↓
        │   signed SignalEnvelope -> PIP
        │
        └── same decision authority replayed by internal PAPER experiments
                ↓
            hypothetical economics only
```

PAPER may evaluate candidate strategies with explicitly labeled development-only inputs,
but PAPER economics cannot validate calibration, cannot self-promote a model/strategy, and
cannot alter the public signal payload.

There is no supported RESEARCH/PAPER/LIVE execution-mode ladder. The public producer is
`bot_v3.py scan/run/status`; the supported internal PAPER interface is
`python -m weatherbot.paper evaluate ...`; real-money execution is not a product direction.

## Current activation state

As of 2026-08-18:

```text
V3 policy                     = frozen / implemented
calibrated probability core   = implemented
accepted V3 artifact          = absent
exact-run activation evidence = pending
accepted-model signals        = fail closed
```

#49/#50 own the final accept/reject and activation evidence. This section must be updated
again before Issue #25 closes so it records the actual final outcome rather than assuming
acceptance.
