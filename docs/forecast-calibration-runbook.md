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

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_build \
  --start-date 2026-04-05 \
  --end-date 2026-08-10 \
  --cache-dir data/calibration/v1/cache \
  --records-out data/calibration/v1/dataset.jsonl \
  --manifest-out data/calibration/v1/dataset-manifest.json \
  --parity-evidence tests/fixtures/forecasting/ecmwf_single_run_parity_2026-04-18.json \
  --request-delay-seconds 0.5
```

Expected logical size if every city/date/horizon is available:

```text
128 target dates × 6 cities × 3 horizons = 2,304 records
```

Do not delete a partially populated cache after a transient provider failure. Fix the
cause and rerun the same command so completed immutable entries are reused.

## 2. Prove offline reproducibility

After the online collection succeeds, rerun with network disabled and write to temporary
output paths:

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_build \
  --start-date 2026-04-05 \
  --end-date 2026-08-10 \
  --cache-dir data/calibration/v1/cache \
  --records-out data/calibration/v1/dataset-offline.jsonl \
  --manifest-out data/calibration/v1/dataset-manifest-offline.json \
  --parity-evidence tests/fixtures/forecasting/ecmwf_single_run_parity_2026-04-18.json \
  --offline

cmp data/calibration/v1/dataset.jsonl data/calibration/v1/dataset-offline.jsonl
cmp data/calibration/v1/dataset-manifest.json data/calibration/v1/dataset-manifest-offline.json
```

Both comparisons must be byte-identical. On Windows PowerShell, use `Compare-Object` or a
binary/hash comparison instead of `cmp`.

## 3. Fit once with an untouched final holdout

The initial split uses 100 target dates for training/inner chronological validation and
28 target dates for the untouched final holdout:

```text
training / inner selection: 2026-04-05 .. 2026-07-13
untouched final holdout:     2026-07-14 .. 2026-08-10
```

Choose one immutable model version and one explicit UTC artifact timestamp. Reusing those
same values with the same dataset must reproduce the same artifact checksum.

Example:

```bash
PYTHONPATH=. uv run python -m weatherbot.forecasting.calibration_train \
  --records data/calibration/v1/dataset.jsonl \
  --manifest data/calibration/v1/dataset-manifest.json \
  --model-version issue12-v1 \
  --created-at-utc 2026-08-12T16:00:00Z \
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

## 4. Review the holdout before scanner integration

Do not integrate the artifact just because fitting succeeded. Review at least:

- validation sample count and fallback coverage;
- forecast bias, MAE, and RMSE;
- mean realized-bin log score;
- mean ranked probability score;
- reliability bins;
- calibrated-versus-fixed-2°F score deltas;
- fitted group counts and sparse fallback levels;
- any city/horizon with insufficient evidence.

A worse calibrated holdout than the fixed-2°F baseline is evidence to revisit the model or
data contract, not a reason to hide the comparison.

## 5. Commit only reviewed evidence

The raw cache is working data and can be large. Before committing generated evidence,
review repository hygiene and decide which normalized files belong in Git versus external
artifact storage. At minimum, the pull request needs reproducible checksums and access to
the exact normalized dataset/manifest used to produce the reviewed model artifact.

Only after the holdout is accepted should `bot_v3.py` be changed to load the compatible
artifact, remove `SIGMA_F = 2.0` / `get_sigma(...)`, persist model provenance on accepted
signals, and rename user-facing `true_prob` language to `model_probability` or equivalent.
