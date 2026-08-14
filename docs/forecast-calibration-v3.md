# Forecast calibration V3 policy

Issue #47 freezes the V3 calibration policy independently of the final forward holdout.

## Policy identifiers

Legacy V1/V2 behavior remains reproducible under `legacy-family-selection-v1`. The V3 fitter is identified as `v3-normal-runtime-v1`.

V3 still fits and scores both bias-adjusted normal and raw empirical residual candidates on the chronological inner-development split. Those candidate scores remain diagnostics only. The raw unsmoothed empirical ECDF is not eligible for inclusion in a V3 artifact.

For every candidate group that satisfies the minimum-sample policy, V3 refits a bias-adjusted normal distribution over the complete allowed training sample. The group is included only when that fit has finite positive variance. Otherwise the group is omitted and consumers follow the existing hierarchy:

```text
city + source + lead + season
  -> climate-region + source + lead + season
  -> source + lead + season
  -> source + lead
  -> source
```

No epsilon variance, minimum synthetic sigma, raw empirical substitute, or fixed-2°F fallback is introduced by V3.

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

The artifact itself remains schema version 1 and contains only groups eligible for probability evaluation. The fitting policy and evaluation kind belong to the training report, not the immutable runtime artifact schema.

## Evidence labels

The dedicated V3 trainer requires an explicit evaluation kind:

- `development` for replay or model-development evidence;
- `final_holdout` for the separately controlled final evaluation.

For Issue #47, real observations may end no later than **2026-08-10**. The previously inspected `2026-07-14..2026-08-10` interval is development evidence only and must never be described as untouched V3 evidence.

The registered final forward holdout is `2026-08-11..2026-08-24`. Issue #49 owns its one-time evaluation after the existing finalization guard. Issue #47 must not read those outcomes.

## Fast evaluator

`CalibratedTemperatureModel` now builds an immutable `CalibrationGroupKey -> CalibrationGroup` index once at construction. Probability calls reuse that index while retaining the same five-level fallback order, probability mathematics, provenance fields, artifact serialization, and artifact checksum.

This change deliberately does not vectorize probability calculations or change the scoring formulas; it removes repeated dictionary reconstruction only.

## Development replay contract

A safe Issue #47 real-data replay uses only the already-inspected dataset:

```text
dataset:             2026-04-05 .. 2026-08-10
training:            2026-04-05 .. 2026-07-13
development replay:  2026-07-14 .. 2026-08-10
model version:       issue12-v3-dev-replay
policy:              v3-normal-runtime-v1
evaluation kind:     development
```

Its artifact and report are development evidence only. They must not be configured as an accepted production artifact and must not be used as the final V3 acceptance decision.

### Recorded Issue #47 replay

The closure replay used the immutable normalized V2 evidence artifact produced by successful workflow run `31717108833`. No forecast or observation collection was performed during the V3 replay.

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

Two V3 runs used the same inputs and the same frozen creation timestamp. Both produced byte-identical artifacts and reports:

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

These scores are useful only as development evidence. They did not change the pre-registered V3 policy and do not accept V3 for runtime use. Final acceptance or rejection remains exclusively owned by Issue #49.

## Scope boundary

Issue #47 does not remove the scanner's fixed-2°F path, activate an artifact, persist PAPER provenance, or accept/reject V3. Those responsibilities remain in Issues #48 and #49.
