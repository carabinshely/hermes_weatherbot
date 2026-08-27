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

The accepted V3 runtime is additionally fail-closed on forecast-run provenance. A public
probability call must carry the exact compatible ECMWF 18Z model-run vintage derived by the
calibration contract for the target date and lead. Missing or mismatched model-run identity
is rejected rather than silently treated as compatible.

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

The final V3 policy was frozen before the untouched holdout and was not changed after
holdout outcomes were inspected.

## Time separation and final holdout

Development/model-selection evidence ends at `2026-08-10`.

```text
final untouched holdout = 2026-08-11 .. 2026-08-24
```

Issue #49 evaluated this interval exactly once after the all-market finalized-history guard
opened on `2026-08-26`. The frozen decision rule required every mechanical integrity gate
to pass and required both proper probabilistic scores to be strictly better than the fixed
`2°F` baseline on the same holdout.

The final result was **ACCEPT**:

```text
validation samples:                 252 / 252
holdout exclusions/source gaps:     0
online/offline normalized replay:   byte-identical
artifact/report reproduction:       byte-identical
forecast bias:                      +0.031349°F
MAE:                                2.573413°F
RMSE:                               3.119199°F
V3 mean log score:                  2.445890
fixed-2°F mean log score:           2.813860
V3 mean ranked probability score:   1.618814
fixed-2°F ranked probability score: 1.847297
fitted runtime groups:              76
omitted runtime groups:             0
```

Reliability was not perfect: several middle probability bands were under-confident, with
observed frequency above mean predicted probability. No post-hoc rejection threshold was
invented after seeing the holdout because none had been pre-registered. This remains
monitoring evidence for future model development; the accepted model must not be retuned
and rescored on this interval as though it were untouched.

Authoritative final evidence is the Issue #49 acceptance record and successful one-shot run
`32977283488`; the compact reviewed bundle is retained under
`evidence/calibration/issue12-v3-final-holdout/`.

## Artifact / approval contract

A runtime artifact is immutable and content-addressed. It records model/source contracts,
training/validation ranges, dataset/manifest identity, minimum-sample policy, fitted groups,
distribution parameters, and diagnostics.

Mechanical validity is not acceptance. Separate repository-controlled approval evidence
must pin an accepted artifact. Missing, malformed, rejected, corrupt, checksum-mismatched,
or source-incompatible approval/artifact state fails closed. There is no fixed-sigma
fallback.

The accepted V3 state is pinned as:

```text
model version:       issue12-v3-final-holdout
artifact identity:   b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550
artifact path:       artifacts/calibration/accepted/b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550.json
approval path:       config/calibration-approval.json
training cutoff:     2026-08-10
acceptance reference: Issue #49 final ACCEPT comment
```

The canonical artifact identity above is the content address verified by the runtime
loader. The JSON file byte SHA-256 is separately recorded in the compact evidence bundle
and must not be substituted for the canonical artifact identity.

Rejected V1/V2 identities remain explicitly blocked by the runtime loader even if a future
configuration attempts to label them accepted.

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

Acceptance of V3 authorizes calibrated signal probability generation only. It does not
authorize a wallet, exchange writes, order placement, cancellation, redemption, customer
execution, or any other live-money capability.

## Current activation state

As of 2026-08-26:

```text
V3 policy                         = frozen / implemented / accepted
calibrated probability core       = implemented
accepted V3 artifact              = pinned / content-addressed
separate repository approval      = pinned to #49 acceptance evidence
exact-run provenance enforcement  = enabled / fail-closed
accepted-model signal generation  = enabled for compatible inputs
live-money execution              = unsupported / out of scope
```

A repository containing the approval and accepted artifact can load the accepted model.
Actual signal generation still fails closed for missing or incompatible forecast-run
provenance and remains subject to the public producer's independent strategy/evidence
rules. Artifact acceptance is therefore not a bypass around input provenance, producer
policy, PIP publication boundaries, or the explicit non-execution product boundary.
