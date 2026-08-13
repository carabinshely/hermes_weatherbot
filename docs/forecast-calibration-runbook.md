# Issue #12 calibration runbook

This runbook performs the expensive historical collection locally and then fits the
versioned calibration artifact from the frozen dataset. It intentionally keeps the full
network sweep out of GitHub-hosted CI.

## Prerequisites

Use Python 3.12 or 3.13 and the repository lock file:

```bash
uv sync --locked --all-groups
```

Run all commands from the repository root on branch
`agent/issue-12-calibrated-uncertainty`.

## 1. Collect the frozen historical dataset

The recommended first v1 sweep is 2026-04-05 through 2026-08-10. The Single Runs archive
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

## 2. Prove offline reproducibility

After the online collection succeeds, rerun with network disabled and write to temporary
output paths:

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

## 3. Fit once with an untouched final holdout

The initial split is defined by calendar date, not by a required record count:

```text
training / inner selection: 2026-04-05 .. 2026-07-13
untouched final holdout:     2026-07-14 .. 2026-08-10
```

Coverage exclusions may reduce the number of samples inside either interval. Review that
missingness explicitly before accepting the model; do not move the split to improve scores.

Choose one immutable model version and one explicit UTC artifact timestamp. Reusing those
same values with the same dataset must reproduce the same artifact checksum.

Example:

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_train \
  --records data/calibration/v1/dataset.jsonl \
  --manifest data/calibration/v1/dataset-manifest.json \
  --model-version issue12-v1 \
  --created-at-utc 2026-08-13T10:10:00Z \
  --training-end 2026-07-13 \
  --validation-start 2026-07-14 \
  --validation-end 2026-08-10 \
  --min-sample-count 30 \
  --artifact-out data/calibration/v1/calibration-artifact.json \
  --report-out data/calibration/v1/holdout-report.json
```

The trainer refuses a gap between training and validation, refuses a validation end that
does not equal the dataset end, revalidates the dataset/manifest checksums before fitting,
and records the fixed-2°F baseline beside the calibrated holdout scores.

## 4. Review the exclusions and holdout before scanner integration

Do not integrate the artifact just because fitting succeeded. Review at least:

- excluded city/date count, reasons, raw-page hashes, and whether exclusions cluster by
  city, season, or holdout period;
- validation sample count and fallback coverage;
- forecast bias, MAE, and RMSE;
- mean realized-bin log score;
- mean ranked probability score;
- reliability bins;
- calibrated-versus-fixed-2°F score deltas;
- fitted group counts and sparse fallback levels;
- any city/horizon with insufficient evidence.

A worse calibrated holdout than the fixed-2°F baseline is evidence to revisit the model or
data contract, not a reason to hide the comparison. Likewise, material or systematically
clustered observation exclusions are a data-quality finding that must be reviewed before
acceptance.

## 5. Commit only reviewed evidence

The raw cache is working data and can be large. Before committing generated evidence,
review repository hygiene and decide which normalized files belong in Git versus external
artifact storage. Do not commit the raw Weather Underground/Open-Meteo cache.

At minimum, the pull request needs reproducible checksums and access to the exact normalized
dataset, manifest, exclusion report, model artifact, and holdout report used for review.

Only after the holdout and missingness evidence are accepted should `bot_v3.py` be changed
to load the compatible artifact, remove `SIGMA_F = 2.0` / `get_sigma(...)`, persist model
provenance on accepted signals, and rename user-facing `true_prob` language to
`model_probability` or equivalent.
