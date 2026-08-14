# Forecast uncertainty calibration

Issue #12 replaces the production fixed `2°F` uncertainty assumption with a versioned,
reproducible residual-distribution model. This document defines the correctness boundary
for that work. It does not claim profitability.

## Core identity

For a market-local target date, define the forecast residual as:

```text
residual = finalized_observed_daily_high - point_in_time_forecast_daily_high
```

The runtime model estimates the residual distribution for the most specific supported
location/source/lead/season group and converts that distribution into probabilities over
the same `TemperatureBucket` boundaries used by parsing and settlement.

The production interface is therefore a residual CDF, not a `sigma` lookup. A fitted
normal group may contain `bias_f` and `sigma_f`, but those are artifact parameters rather
than global configuration.

## Forecast source contract

Current production signal generation uses Open-Meteo ECMWF IFS 0.25° daily maximum
2-metre temperature for the market-local date.

Historical fitting data is acceptable only when it reproduces that effective forecast
contract closely enough to be treated as the same source. The dataset manifest must
record at least:

- Open-Meteo endpoint/archive family;
- model identifier and resolution;
- coordinates and elevation/downscaling semantics;
- timezone and local target date;
- daily-high aggregation semantics;
- forecast lead definition;
- run/as-of/retrieval provenance available from the archive;
- raw payload hash and normalized-record hash.

### Previous Runs is not automatically equivalent

Open-Meteo's Previous Model Runs API exposes fixed lead-time variables such as
`temperature_2m_previous_day1`, representing the value predicted 24 hours before each
valid timestamp. It is useful for forecast-skill and bias analysis and has broad archive
coverage, but an hourly fixed-offset series is not assumed to equal the daily maximum
from one specific production model run.

Before Previous Runs records can train the production IFS 0.25° artifact, an overlap test
must compare reconstructed historical daily highs with captured production daily-high
forecasts. The comparison report, tolerances, sample counts, dates, and mismatches are
part of the dataset manifest. Material mismatch creates a distinct source contract; it
cannot be hidden by calibration.

Official reference:
https://open-meteo.com/en/docs/previous-runs-api

### Single Runs and possible HRES migration

Open-Meteo's Single Runs API preserves individual model runs and is the preferred shape
for exact forecast reproduction. Native ECMWF IFS HRES 9 km single runs have materially
longer historical coverage than many other model/run combinations.

That does **not** authorize silently changing production from IFS 0.25° to HRES 9 km. A
source migration requires:

1. a new `ForecastSource` / source-contract identifier;
2. a reproducible historical artifact fitted only to that source;
3. untouched holdout comparison against the current source;
4. explicit runtime configuration/migration;
5. signal provenance recording the new source and model version.

Official references:
https://open-meteo.com/en/docs/single-runs-api
https://open-meteo.com/en/docs/ecmwf-api

### Validated IFS 0.25° Single Runs reconstruction

The repository contains legacy production forecast snapshots from the morning of
2026-04-18 for New York City, Chicago, Miami, Dallas, Seattle, and Atlanta, with D+0,
D+1, and D+2 target dates. The historical code commit
`d05c077294b95be5557d067546dab49ca24863b5` predates those captures and proves that the
stored ECMWF values were produced from:

```text
Open-Meteo /v1/forecast
daily=temperature_2m_max
models=ecmwf_ifs025
temperature_unit=fahrenheit
bias_correction=true
market-local timezone
```

The legacy persistence path stored those values only after Python `round()`, so the
original sub-degree production values and raw API payloads are unavailable. Archive
parity must therefore state its precision honestly.

For the exact ECMWF run initialized at `2026-04-17T18:00:00Z`, Open-Meteo Single Runs
cannot directly compute `daily=temperature_2m_max` for U.S. market timezones because the
run does not begin at local midnight. The validated reconstruction instead requests the
same model/run/coordinates/timezone/bias-correction as hourly `temperature_2m`, groups the
returned timestamps by market-local `YYYY-MM-DD`, and takes the maximum for each local
day.

Against the stored legacy production snapshots this reconstruction produced:

```text
6 cities × 3 target dates = 18 pairs
18 / 18 exact matches after the historical whole-degree production rounding
raw archive maximum vs rounded reference MAE: 0.3056°F
maximum absolute raw-vs-rounded-reference difference: 0.5°F
```

The same result holds for both the earliest snapshots at approximately 06:33 UTC and the
latest snapshots at approximately 07:35 UTC. As a run-identity counterexample, Atlanta's
`2026-04-18T00:00:00Z` run matches only 1 of the 3 stored target temperatures after
rounding, while the 18Z run matches all 3.

The normalized evidence, response hashes, reference-file hashes, and counterexample are
committed as:

```text
tests/fixtures/forecasting/ecmwf_single_run_parity_2026-04-18.json
```

This evidence validates the Single Runs hourly/local-day-max reconstruction at the
**precision the legacy production bot actually retained**. It does not prove byte-identical
API payloads or sub-degree identity. Historical calibration records therefore retain a
distinct archive capture-contract ID and may enter a production-source dataset only
through an explicit passing parity gate.

## Observation target contract

Training truth must match the market's declared resolution measurement as closely as is
reproducibly possible: station/location, market-local date, unit, and finalized daily-high
measurement basis.

The #13 observation ledger already records the declared source, source URL, station,
measurement basis, date/timezone, revision, and source payload hash. Those fields define
the target identity used for learning and for calibration parity checks.

An independent archive such as NOAA/NCEI may be used for diagnostics, gap analysis, or a
separately versioned training source. It may be treated as equivalent to the declared
source only after overlap evidence demonstrates the equivalence and the limitations are
recorded. Nearby-station or reanalysis temperatures are not silently interchangeable
with the settlement target.

## Group hierarchy

The initial deterministic fallback order is:

```text
city + source + D+n + season
  -> climate region + source + D+n + season
  -> source + D+n + season
  -> source + D+n
  -> source
```

Seasons use the market-local target date:

- DJF: December-February
- MAM: March-May
- JJA: June-August
- SON: September-November

A group below the artifact's minimum sample count is ineligible. If every fallback level
is ineligible, the scanner rejects probability generation. There is no fixed-sigma
fallback.

## Distribution selection

At minimum the fitter evaluates:

- a bias-adjusted normal residual distribution;
- an empirical residual distribution.

Normality diagnostics are retained as evidence, but distribution choice is based on a
chronological inner-validation probabilistic score. The final holdout is not used for
selection.

The empirical distribution exists because residuals can be skewed, heavy-tailed, or
multi-modal. The normal distribution remains useful when its simpler shape validates
well. Neither is assumed correct globally.

## Time separation

The artifact has three temporal regions:

```text
training / inner forward validation  |  final untouched holdout
-------------------------------------|--------------------------
                   training_end      validation_start
```

All fitted parameters and distribution choices are fixed before `validation_start`.
Every final holdout date is later than `training_end`.

The holdout report contains at least forecast bias, MAE, RMSE, realized-bin log score,
ranked probability score over ordered temperature thresholds, reliability bins, sample
counts, and the same probabilistic scores for the legacy fixed-`2°F` baseline.

## Artifact identity

A runtime artifact is immutable and content-addressed. It records:

- schema version and model version;
- forecast and observation contract identifiers;
- training and validation ranges;
- normalized dataset/manifest SHA-256;
- minimum sample policy;
- every fitted group and sample count;
- selected distribution and its parameters/evidence;
- normality and selection diagnostics.

The serialized artifact carries a SHA-256 over its canonical payload. Runtime loading
rejects checksum, schema, or source incompatibility.

## Runtime provenance

Every accepted probability retains enough information to identify the exact artifact and
fallback decision:

```text
model_version
artifact_sha256
forecast_source
calibration_group_key
fallback_level
distribution_type
calibration_sample_count
training_cutoff
model_probability
```

Research and paper modes must use the same probability boundary. A future live backend
must reuse it rather than implementing separate probability math.

## Runtime approval boundary

A calibration artifact has three distinct states and they must not be conflated:

```text
mechanically valid
      ↓
scientifically accepted
      ↓
execution enabled
```

A mechanically valid artifact has a supported schema, a valid internal checksum, and
compatible source contracts. That is necessary but not sufficient for runtime use.
Scientific acceptance is a separate review decision made from the frozen validation
protocol. Runtime records that decision in exactly one repository-controlled manifest:

```text
config/calibration-approval.json
```

The normal runtime path never scans an artifact directory, selects a newest file, accepts
an arbitrary CLI path, or follows an environment-variable override. The approval manifest
pins one content-addressed artifact under:

```text
artifacts/calibration/accepted/<artifact_sha256>.json
```

The manifest must record the accepted model version, artifact SHA-256, forecast and
observation contracts, acceptance reference, and UTC acceptance time. The loader verifies
those claims again against the artifact before constructing the calibrated runtime model.
An artifact therefore cannot authorize itself merely by containing valid metadata.

The two rejected development artifacts are additionally denied by exact SHA-256 as a
defense-in-depth regression guard. The approval allowlist remains the actual activation
mechanism.

## Fail-closed activation semantics

Until an accepted manifest and artifact are committed, calibrated probability loading
fails before weather or market collection. Missing, malformed, rejected, corrupt,
path-escaping, checksum-mismatched, or source-incompatible approval evidence never falls
back to a global uncertainty constant.

Issue #48A intentionally activates only the research probability boundary. During this
phase:

```text
RESEARCH strategy probability  = calibrated artifact when separately accepted
PAPER strategy scanning         = disabled
LIVE strategy scanning          = disabled
fixed 2°F fallback              = absent
```

Adding an accepted artifact after the untouched V3 holdout must therefore not enable
PAPER or LIVE execution implicitly. Those modes require a separate explicit integration
change. Mechanical PAPER ledger, recovery, sizing, risk, and status tests remain valid
independently of whether the weather strategy is permitted to feed them a probability.

## Scanner-facing probability object

Runtime callers consume one immutable probability result containing both the number and
its calibration provenance. The scanner does not separately reconstruct the forecast
source, market date, or signal temperature; those are taken from the authoritative
`WeatherInputSnapshot` used for the decision.

This gives one decision-time identity:

```text
WeatherInputSnapshot
+ city / climate region / lead
+ TemperatureBucket
+ accepted artifact
        ↓
CalibratedProbability
```

The resulting object carries `model_probability` together with the model version,
artifact SHA-256, forecast source, selected calibration group and fallback level,
distribution type, sample count, and training cutoff. Research output persists these
fields as a unit rather than storing a probability without its model identity.


### Runtime lead-domain and RESEARCH evidence

The frozen calibration dataset supports only D+0, D+1, and D+2. Runtime probability evaluation rejects any other lead instead of falling through to a broader group trained on a different lead domain. The RESEARCH scanner requests exactly those supported horizons.

Every emitted RESEARCH signal is appended to `state/research-signals.jsonl` before it is reported as an observed signal. Each JSON line retains the weather snapshot metadata, complete calibration provenance (`model_version`, `artifact_sha256`, `forecast_source`, `calibration_group_key`, `fallback_level`, `distribution_type`, `calibration_sample_count`, `training_cutoff`, and `model_probability`), and validated quote metadata. Persistence failure rejects that candidate rather than emitting unauditable research evidence.


### Runtime forecast-vintage gate

The calibrated residuals are tied to the frozen market-local decision policy: D+0/D+1/D+2 are sampled from the previous UTC calendar day's 18Z ECMWF IFS 0.25° run at the 00:15 market-local decision point. Runtime therefore accepts probability generation only when the production forecast is retrieved in the narrow 00:15-00:25 market-local decision window for the corresponding decision day. The public scanner checks this window before weather or market network work, and the calibrated probability boundary checks the forecast retrieval timestamp independently. If model-run initialization metadata is available, it must also equal the expected previous-day 18Z run. A later continuously updated forecast cannot silently reuse the frozen residual distribution.

Continuous RESEARCH mode probes on the decision-window cadence so each U.S. timezone can be evaluated near its own market-local decision point; mechanical resolution monitoring continues between probes.


The current stitched production forecast parser does not invent model-run identity. If the provider cannot supply `model_run_initialized_at_utc`, calibrated runtime evaluation rejects the candidate even inside the decision window. This is deliberate: Open-Meteo documents the operational Forecast API as a continuously stitched latest-run series, while Single Runs is the exact-run interface. A later provider change may supply provable run identity, but #48A never guesses it from wall-clock time alone.
