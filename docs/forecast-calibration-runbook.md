# Issue #12 calibration runbook

This runbook documents the historical collection, reproducible fitting, final one-shot V3
holdout, and accepted-artifact promotion path. Expensive provider collection remains
outside ordinary GitHub-hosted CI, and raw cache/working datasets remain outside Git.

The current V3 policy is documented in `docs/forecast-calibration-v3.md`.
`2026-07-14..2026-08-10` is **development evidence**, not an untouched holdout. The
registered final V3 holdout `2026-08-11..2026-08-24` was evaluated exactly once by Issue
#49 after the finalized-history guard opened and must not be evaluated again as an
untouched holdout.

## Prerequisites

Use Python 3.12 or 3.13 and the repository lock file:

```bash
uv sync --locked --all-groups
```

Historical collection/reproduction work starts from the Issue #12 calibration branch.
Issue #47 V3 development replay used the stacked `agent/issue-47-v3-policy` branch. The
final accepted state is carried by the Issue #12 branch with the content-addressed artifact
and separate approval file.

## 1. Collect the frozen development dataset

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
Actions reruns, preserve the calibration cache deliberately rather than treating a fresh
runner workspace as equivalent evidence.

Issue #47 closure did **not** require a fresh network sweep. It reused the already-frozen
2026-04-05..2026-08-10 dataset and cache evidence produced by the calibration branch.

## 2. Prove offline reproducibility

After an online collection succeeds, rerun with network disabled and write to temporary
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

For an Issue #47 V3 replay, verify the existing dataset manifest before fitting and require:

```text
start_date = 2026-04-05
end_date   = 2026-08-10
```

Abort if any real observation/market date after 2026-08-10 appears in the development replay
inputs.

## 3. Historical V1/V2 reproduction

The old chronological split was:

```text
training:               2026-04-05 .. 2026-07-13
historical evaluation:  2026-07-14 .. 2026-08-10
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

Do not use a rejected V1/V2 artifact for runtime configuration. The strict runtime also
blocks the known rejected V1/V2 artifact identities even if an approval file is forged to
say otherwise.

## 4. Reproduce the frozen V3 development replay

Issue #47 replayed the already-inspected July 14-August 10 interval **only as development
evidence** using the dedicated V3 trainer:

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
baseline comparison, but do not reinterpret those scores as final holdout evidence.

The development artifact is not the accepted runtime artifact and must not be configured as
such.

## 5. Final V3 holdout — historical record, do not rerun

The registered final forward V3 holdout was:

```text
2026-08-11 .. 2026-08-24
```

Issue #49 evaluated it exactly once after the all-market two-day finalized-history guard
opened on `2026-08-26`. The successful one-shot workflow run is `32977283488`; its frozen
scientific source is `fa9f389e21861d66599367160fcac58763b7dec7`.

The final execution extended the preserved cache only through `2026-08-24`, verified that
all pre-existing cache objects remained byte-identical, built the normalized dataset,
proved byte-identical offline replay, fitted V3 with training data ending `2026-08-10`,
evaluated `2026-08-11..2026-08-24`, and reproduced the artifact/report without provider
re-read.

Final integrity/result summary:

```text
validation samples:                 252 / 252
holdout exclusions/source gaps:     0
online/offline replay:              byte-identical
artifact/report reproduction:       byte-identical
fitted groups:                      76
omitted groups:                     0
V3 mean log score:                  2.4458898555
fixed-2°F mean log score:           2.8138595051
V3 mean ranked probability score:   1.6188143448
fixed-2°F ranked probability score: 1.8472967783
final decision:                     ACCEPT
```

Do **not** rerun this interval for model selection, threshold tuning, or another purportedly
untouched evaluation. Reliability evidence, including under-confidence in several middle
probability bands, is retained for monitoring/future-development work only.

## 6. Promote the accepted artifact without re-reading providers

Issue #50 promotion is an offline evidence operation. It must consume the already-reviewed
successful Issue #49 artifact rather than refreshing weather/observation sources or fitting
a modified policy.

Accepted runtime identity:

```text
model version:       issue12-v3-final-holdout
canonical SHA-256:   b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550
artifact path:       artifacts/calibration/accepted/b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550.json
approval path:       config/calibration-approval.json
```

The approval is separate from the artifact and points to the authoritative Issue #49 final
acceptance record. The artifact path is content-addressed by the canonical artifact identity
verified by the runtime loader. The artifact JSON file byte SHA-256 is separate transport
evidence and must not be substituted as `artifact_sha256` in approval.

The reviewed compact evidence lives at:

```text
evidence/calibration/issue12-v3-final-holdout/
```

It contains the manifest, exclusion report, holdout report, compact review summary,
checksums, provenance, and README. It intentionally excludes:

- the raw provider HTTP cache;
- cache-before/cache-after working snapshots;
- the multi-megabyte normalized `dataset.jsonl`.

Those larger inputs remain reproducibility working data and source-workflow artifacts, not
repository runtime payloads.

## 7. Validate exact compatible runtime provenance

Loading the accepted artifact is necessary but not sufficient. Before a model probability
can back a public signal, the forecast input must carry explicit model-run provenance
matching the exact calibration-compatible ECMWF 18Z vintage for the target date and lead.

Repository tests must retain all three cases:

1. the pinned accepted V3 artifact loads and evaluates a compatible exact-run forecast;
2. a different model-run vintage is rejected;
3. missing model-run provenance is rejected.

The broader runtime tests must continue to prove missing/malformed/rejected approvals,
corrupt or checksum-mismatched artifacts, contract mismatches, decision-window violations,
and known rejected V1/V2 identities fail closed.

Do not weaken provenance checks to make an operational forecast fit the artifact. If the
runtime forecast cannot prove the compatible run identity, probability generation must
reject and the source/provenance integration must be fixed separately.

## 8. Review final evidence and repository scope

For the accepted V3 state, retain at least:

- one-shot workflow run and artifact identity;
- frozen scientific source SHA;
- canonical accepted artifact identity and artifact-file byte SHA-256;
- dataset and manifest identities;
- validation sample count and holdout exclusion count;
- byte-identical online/offline and artifact/report replay results;
- forecast bias, MAE, RMSE;
- both proper-score comparisons against fixed 2°F;
- reliability evidence;
- fitted/omitted group counts and runtime distribution types;
- separate approval reference and exact-run compatibility tests.

No final-evidence update may modify the frozen scientific policy and then reuse the August
11-24 holdout as though it were untouched.

## 9. Product boundary after acceptance

The accepted artifact enables calibrated probability semantics for compatible public signal
inputs. Internal PAPER experiments may replay the same accepted model semantics, but PAPER
state/economics remain separate from real signal identity, publication eligibility, and
model acceptance.

This runbook does not authorize or describe a supported wallet, live exchange-write, order,
cancellation, redemption, customer-execution, or copy-trading path. V3 acceptance and
Issue #50 activation are signal-generation/runtime-provenance decisions only.
