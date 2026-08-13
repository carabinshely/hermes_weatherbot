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

## Scope boundary

Issue #47 does not remove the scanner's fixed-2°F path, activate an artifact, persist PAPER provenance, or accept/reject V3. Those responsibilities remain in Issues #48 and #49.
