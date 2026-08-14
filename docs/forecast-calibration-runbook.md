# Issue #12 calibration runbook

This runbook performs the expensive historical collection locally and then fits versioned
calibration artifacts from a frozen dataset. It intentionally keeps the full network sweep
out of GitHub-hosted CI.

The current V3 policy is documented in `docs/forecast-calibration-v3.md`. In particular,
`2026-07-14..2026-08-10` is now **development evidence**, not an untouched holdout. The
registered final V3 holdout is `2026-08-11..2026-08-24` and belongs exclusively to Issue
#49 after the existing finalization guard.

## Prerequisites

Use Python 3.12 or 3.13 and the repository lock file:

```bash
uv sync --locked --all-groups
```

Historical collection/reproduction work starts from the Issue #12 calibration branch.
Issue #47 V3 development replay runs from the stacked `agent/issue-47-v3-policy` branch.

## 1. Collect the frozen historical dataset

The frozen development dataset is 2026-04-05 through 2026-08-10. The Single Runs archive
documents most model archives from 2026-04-02. Because the calibration dataset includes
D+2 forecasts, the April 5 target date is the first date whose required decision-day run
stays inside that documented archive window.

The collector is resumable. Every successful HTTP response is frozen as raw bytes plus a
metadata sidecar before parsing. Re-running the same command replays completed cache
entries rather than refreshing them.

Real airport history is not guaranteed to contain a complete observation series for every
calendar day. The production Weather Underground parser remains fail-closed on incomplete
coverage. The historical sweep does **not** weaken that rule; instead it excludes only
coverage-deficient city/date observations and writes a deterministic exclusion report with
the frozen raw-page hash and exact rejection reason. Station/date/timezone mismatches,
malformed pages, non-whole-degree highs, cache corruption, and other contract violations
still abort the sweep.

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_sweep \
  --start-date 2026-04-05 \
  --end-date 2026-08-10 \
  --cache-dir data/calibration/v1/cache \
  --records-out data/calibration/v1/dataset.jsonl \
  --manifest-out data/calibration/v1/dataset-manifest.json \
  --exclusions-out data/calibration/v1/dataset-exclusions.json \
  --parity-evidence tests/fixtures/forecasting/ecmwf_single_run_parity_2026-04-18.json \
  --request-delay-seconds 0.5
```

The maximum logical size is:

```text
128 target dates × 6 cities × 3 horizons = 2,304 records
```

Each excluded city/date removes exactly the D+0, D+1, and D+2 records for that observation.
The exclusion report must account for the difference between 2,304 and the collected record
count. Do not silently fill a missing day, relax the Weather Underground coverage policy,
or substitute another observation source.

Do not delete a partially populated cache after a provider or parser failure. Fix the cause
and rerun the same command so completed immutable entries are reused. For self-hosted
Actions reruns, reuse the same runner `_work` directory and keep `checkout` configured with
`clean: false`; moving to a fresh runner workspace discards the preserved HTTP cache.

Issue #47 closure must **not** run a fresh network sweep. Reuse the already-frozen
2026-04-05..2026-08-10 dataset and cache evidence produced by the calibration branch.

## 2. Prove offline reproducibility

After the original online collection succeeds, rerun with network disabled and write to
temporary output paths:

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_sweep \
  --start-date 2026-04-05 \
  --end-date 2026-08-10 \
  --cache-dir data/calibration/v1/cache \
  --records-out data/calibration/v1/dataset-offline.jsonl \
  --manifest-out data/calibration/v1/dataset-manifest-offline.json \
  --exclusions-out data/calibration/v1/dataset-exclusions-offline.json \
  --parity-evidence tests/fixtures/forecasting/ecmwf_single_run_parity_2026-04-18.json \
  --offline

cmp data/calibration/v1/dataset.jsonl data/calibration/v1/dataset-offline.jsonl
cmp data/calibration/v1/dataset-manifest.json data/calibration/v1/dataset-manifest-offline.json
cmp data/calibration/v1/dataset-exclusions.json data/calibration/v1/dataset-exclusions-offline.json
```

All three comparisons must be byte-identical. The exclusion report is part of the
reproducibility evidence, not an informational log. On Windows PowerShell, use a binary or
hash comparison instead of `cmp`.

For an Issue #47 V3 replay, verify the existing dataset manifest before fitting and require:

```text
start_date = 2026-04-05
end_date   = 2026-08-10
```

Abort if any real observation/market date after 2026-08-10 appears in the replay inputs.

## 3. Historical V1/V2 reproduction

The old chronological split was:

```text
training:             2026-04-05 .. 2026-07-13
historical evaluation: 2026-07-14 .. 2026-08-10
```

V1 and V2 already inspected that second interval. It therefore must never again be called
an untouched V3 holdout. Commands using `weatherbot.forecasting.calibration_train` with
that split are retained only for rejected V1/V2 reproduction and audit.

Example historical reproduction shape:

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_train \
  --records data/calibration/v1/dataset.jsonl \
  --manifest data/calibration/v1/dataset-manifest.json \
  --model-version issue12-v2 \
  --created-at-utc <frozen-historical-timestamp> \
  --training-end 2026-07-13 \
  --validation-start 2026-07-14 \
  --validation-end 2026-08-10 \
  --min-sample-count 30 \
  --artifact-out <historical-artifact-output> \
  --report-out <historical-report-output>
```

Do not use a rejected V1/V2 artifact for runtime configuration.

## 4. Run the frozen V3 development replay

Issue #47 may replay the already-inspected July 14-August 10 interval **only as development
evidence**. Run from `agent/issue-47-v3-policy` using the dedicated V3 trainer:

```text
dataset:             2026-04-05 .. 2026-08-10
training:            2026-04-05 .. 2026-07-13
development replay:  2026-07-14 .. 2026-08-10
model version:       issue12-v3-dev-replay
fitting policy:      v3-normal-runtime-v1
evaluation kind:     development
min sample count:    30
```

Choose one explicit UTC artifact timestamp before the first run and reuse that exact value
for the reproduction run. Write both runs to a temporary directory outside the repository
working tree.

```bash
CREATED_AT_UTC=<one-frozen-utc-timestamp>
OUT=<temporary-directory-outside-the-repository>

PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_v3_train \
  --records data/calibration/v1/dataset.jsonl \
  --manifest data/calibration/v1/dataset-manifest.json \
  --evaluation-kind development \
  --model-version issue12-v3-dev-replay \
  --created-at-utc "$CREATED_AT_UTC" \
  --training-end 2026-07-13 \
  --validation-start 2026-07-14 \
  --validation-end 2026-08-10 \
  --min-sample-count 30 \
  --artifact-out "$OUT/run1-artifact.json" \
  --report-out "$OUT/run1-report.json"

PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_v3_train \
  --records data/calibration/v1/dataset.jsonl \
  --manifest data/calibration/v1/dataset-manifest.json \
  --evaluation-kind development \
  --model-version issue12-v3-dev-replay \
  --created-at-utc "$CREATED_AT_UTC" \
  --training-end 2026-07-13 \
  --validation-start 2026-07-14 \
  --validation-end 2026-08-10 \
  --min-sample-count 30 \
  --artifact-out "$OUT/run2-artifact.json" \
  --report-out "$OUT/run2-report.json"

cmp "$OUT/run1-artifact.json" "$OUT/run2-artifact.json"
cmp "$OUT/run1-report.json" "$OUT/run2-report.json"
```

Both comparisons must be byte-identical. The report must state:

```text
fitting_policy   = v3-normal-runtime-v1
evaluation_kind  = development
validation_start = 2026-07-14
validation_end   = 2026-08-10
```

Review the development scores, diagnostics, fitted/omitted group counts, and fixed-2°F
baseline comparison, but **do not tune the frozen V3 policy from those scores**. Mechanical
failures, checksum mismatches, non-determinism, unsafe runtime distributions, or broken
fallback behavior block Issue #47. Better or worse development scores do not accept or
reject V3.

The development artifact is not an accepted runtime artifact and must not be configured by
Issue #48.

## 5. Quarantine the final V3 holdout

The registered final forward V3 holdout is:

```text
2026-08-11 .. 2026-08-24
```

Issue #47 must not read those outcomes. Issue #49 owns the one-time final evaluation after
the complete interval is finalized under the two-day guard. Do not extend the Issue #47
dataset, inspect provider outcome pages for those dates, or place those observations in any
fixture/report used by the V3 closure work.

## 6. Review development evidence before closing Issue #47

Record at least:

- dataset and manifest SHA-256 identities;
- development artifact and report SHA-256 identities;
- byte-identical reproduction result;
- evaluation sample count;
- forecast bias, MAE, and RMSE;
- mean realized-bin log score;
- mean ranked probability score;
- reliability bins;
- calibrated-versus-fixed-2°F score deltas;
- fitted and omitted group counts;
- empirical-versus-normal training diagnostics;
- fallback/group coverage where available.

Label the evidence explicitly as **development-only** and state that it is neither the final
V3 holdout nor an accepted production artifact.

## 7. Runtime integration is separate

Issue #48 may implement the fail-closed research/PAPER calibrated probability boundary in
parallel with the final holdout wait. It may remove the fixed-2°F scanner path and prebuild
complete model provenance handling, but it must configure **no rejected or unaccepted
artifact**.

Activation of a concrete V3 artifact remains blocked on Issue #49 acceptance. Therefore do
not interpret the absence of a final accepted artifact as a reason to delay #48's fail-closed
implementation.

## 8. Commit only reviewed evidence

The raw cache is working data and can be large. Do not commit the raw Weather Underground or
Open-Meteo cache. Do not commit the Issue #47 development artifact merely because the replay
succeeded.

For Issue #47, record compact reproducibility evidence in the pull request: exact command,
branch/head SHA, dataset/manifest SHA-256 values, development artifact/report SHA-256 values,
frozen date boundaries, fitted/omitted counts, main development metrics, and confirmation
that the second run was byte-identical.

Issue #49 will separately decide which final accepted artifact/manifest/exclusion/holdout
evidence belongs in Git if V3 passes the registered final evaluation.
