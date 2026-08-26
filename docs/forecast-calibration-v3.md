# Forecast calibration V3 policy

Issue #47 froze the V3 calibration policy independently of the final forward holdout.
Issue #49 subsequently evaluated that frozen policy exactly once on the registered final
holdout and accepted it without changing the policy after seeing the outcomes.

## Policy identifiers

Legacy V1/V2 behavior remains reproducible under `legacy-family-selection-v1`. The V3 fitter
is identified as `v3-normal-runtime-v1`.

V3 fits and scores both bias-adjusted normal and raw empirical residual candidates on the
chronological inner-development split. Those candidate scores remain diagnostics only. The
raw unsmoothed empirical ECDF is not eligible for inclusion in a V3 artifact.

For every candidate group that satisfies the minimum-sample policy, V3 refits a
bias-adjusted normal distribution over the complete allowed training sample. The group is
included only when that fit has finite positive variance. Otherwise the group is omitted
and consumers follow the existing hierarchy:

```text
city + source + lead + season
  -> climate-region + source + lead + season
  -> source + lead + season
  -> source + lead
  -> source
```

No epsilon variance, minimum synthetic sigma, raw empirical substitute, or fixed-2°F
fallback is introduced by V3.

## Diagnostic evidence versus artifact contents

The V3 training report records every considered group with:

- stable calibration group key and level;
- sample count;
- Jarque-Bera and normality diagnostics;
- normal inner-development CRPS when available;
- empirical inner-development CRPS;
- artifact eligibility;
- selected artifact distribution type when eligible;
- deterministic omission reason when not eligible.

The artifact itself remains schema version 1 and contains only groups eligible for
probability evaluation. The fitting policy and evaluation kind belong to the training
report, not the immutable runtime artifact schema.

## Evidence labels and time separation

The dedicated V3 trainer requires an explicit evaluation kind:

- `development` for replay or model-development evidence;
- `final_holdout` for the separately controlled final evaluation.

Development/model-selection evidence ends at **2026-08-10**. The previously inspected
`2026-07-14..2026-08-10` interval is development evidence only and must never be described
as untouched V3 evidence.

The registered final forward holdout was `2026-08-11..2026-08-24`. Issue #49 evaluated it
exactly once after the all-market finalized-history guard opened on `2026-08-26`. Issue #47
did not read those outcomes while freezing the V3 policy.

## Fast evaluator

`CalibratedTemperatureModel` builds an immutable `CalibrationGroupKey -> CalibrationGroup`
index once at construction. Probability calls reuse that index while retaining the same
five-level fallback order, probability mathematics, provenance fields, artifact
serialization, and artifact checksum.

This deliberately does not vectorize probability calculations or change the scoring
formulas; it removes repeated dictionary reconstruction only.

## Development replay contract

The Issue #47 real-data replay used only the already-inspected dataset:

```text
dataset:             2026-04-05 .. 2026-08-10
training:            2026-04-05 .. 2026-07-13
development replay:  2026-07-14 .. 2026-08-10
model version:       issue12-v3-dev-replay
policy:              v3-normal-runtime-v1
evaluation kind:     development
```

Its artifact and report remain development evidence only. They are not configured as the
accepted runtime artifact and were not used as the final V3 acceptance decision.

### Recorded Issue #47 replay

The closure replay used the immutable normalized V2 evidence artifact produced by successful
workflow run `31717108833`. No forecast or observation collection was performed during the
V3 development replay.

Input evidence:

```text
record count:                    2238
dataset interval:                2026-04-05 .. 2026-08-10
dataset SHA-256:                 b78b9b0622d1061d1947657aa59ede1810dc9a6574a0a3672fc99dc693ceaecb
dataset manifest semantic SHA:   30961b8c8ee501383fa06d8ceb8338d38be88dd63f344fe223ebdf33515e20ce
dataset manifest file SHA-256:   ce77fabf44607ca1b84336721f57fd438012ec7d67307bd24c0347e1d67725c8
exclusion report file SHA-256:   5034e9996d6114643936946fabadeb5c58b45e9be34a6802bcbf12315ad52d4f
source workflow artifact digest: 4ccd4ba4f21c2bb5d68e0d9f960e565b97614ec8ac0aa349de554d823a72396f
```

Two V3 development runs used the same inputs and the same frozen creation timestamp. Both
produced byte-identical artifacts and reports:

```text
model version:                  issue12-v3-dev-replay
policy:                         v3-normal-runtime-v1
evaluation kind:                development
training end:                   2026-07-13
development validation:         2026-07-14 .. 2026-08-10
validation sample count:        504
fitted runtime groups:          76
omitted groups:                 0
embedded artifact SHA-256:      af0b4574850d4ebfeb47916e628d16f66f10a5207b0a363e63420b1b9ecaca18
artifact file SHA-256:          0cf749a6a33cdf49b6e29adb46f2454534f5419fab65b2fd9490be76135d4ed2
report file SHA-256:            a7a055c23056cd678e0ccff2009f1ce2f267ebf5a74761a1cc17917a1a9aebf3
```

Development metrics were:

```text
forecast bias:                  +0.604960°F
MAE:                            2.843056°F
RMSE:                           3.429694°F
mean log score:                 2.516366
fixed-2°F mean log score:       3.062888
delta:                          -0.546522
mean ranked probability score:  1.704312
fixed-2°F ranked score:         2.080291
delta:                          -0.375979
```

These scores remain development-only evidence. They did not accept V3 and did not alter the
pre-registered final policy.

## Final Issue #49 holdout outcome

The successful one-shot final evaluation is workflow run `32977283488`, using frozen
scientific source `fa9f389e21861d66599367160fcac58763b7dec7`. The replacement workflow
head `7c9dd134efc5f62161d12a4ca6329c48d56f1f72` differed only in infrastructure ordering and
checked out that exact frozen scientific source for collection, fitting, and evaluation.

The final holdout contract was:

```text
dataset:             2026-04-05 .. 2026-08-24
training:            through 2026-08-10
final holdout:       2026-08-11 .. 2026-08-24
model version:       issue12-v3-final-holdout
policy:              v3-normal-runtime-v1
evaluation kind:     final_holdout
min sample count:    30
```

Mechanical integrity evidence:

```text
validation samples:               252 / 252
holdout exclusions/source gaps:   0
preserved cache objects changed:  0
online/offline replay:            byte-identical
artifact/report reproduction:     byte-identical
fitted runtime groups:            76
omitted runtime groups:           0
runtime distribution type:        normal
```

The pre-registered decision rule required both proper probabilistic scores to be strictly
better than the fixed-2°F baseline after all integrity gates passed:

```text
mean log score
  V3:          2.4458898555
  fixed-2°F:   2.8138595051
  delta:      -0.3679696496

mean ranked probability score
  V3:          1.6188143448
  fixed-2°F:   1.8472967783
  delta:      -0.2284824335
```

Both conditions passed, so the final Issue #49 decision is **ACCEPT**.

Additional diagnostics were forecast bias `+0.031349°F`, MAE `2.573413°F`, and RMSE
`3.119199°F`. Reliability was not perfect: several middle probability bands were
under-confident. That evidence remains a future-development/monitoring concern; no
post-hoc threshold was invented after seeing the holdout, and this interval must not be
retuned and rescored as if it were untouched.

The accepted artifact is now pinned by Issue #50:

```text
model version:       issue12-v3-final-holdout
canonical identity: b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550
artifact path:       artifacts/calibration/accepted/b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550.json
approval path:       config/calibration-approval.json
```

The compact reviewed final evidence is retained under
`evidence/calibration/issue12-v3-final-holdout/`. The multi-megabyte normalized dataset and
raw provider cache remain outside Git.

## Runtime provenance boundary

Artifact acceptance does not weaken source compatibility. The strict runtime requires
explicit model-run provenance matching the exact calibration-compatible ECMWF 18Z vintage
for the target date and lead. Missing or mismatched model-run identity fails closed.
Rejected V1/V2 identities remain explicitly blocked.

The accepted artifact enables calibrated probability semantics for compatible public
signal inputs and for deterministic internal PAPER replay. PAPER state/economics remain
separate from real signal identity and cannot promote or validate a calibration model.

## Scope boundary

Issue #49 accepted probability quality; Issue #50 pins the accepted artifact and approval
and validates the exact-run runtime boundary. Neither decision authorizes wallet access,
live exchange writes, order placement, cancellation, redemption, customer execution, or
any other live-money capability.
