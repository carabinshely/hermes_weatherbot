from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"patch anchor not found in {path}: {old[:160]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    start_index = source.find(start)
    if start_index < 0:
        raise SystemExit(f"start marker not found in {path}: {start!r}")
    end_index = source.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"end marker not found in {path}: {end!r}")
    file_path.write_text(
        source[:start_index] + replacement + source[end_index:],
        encoding="utf-8",
    )


replace_once(
    "README.md",
    "> **Current #48A activation status:** calibrated strategy scanning is **RESEARCH-only**. `bot_v3.py scan/run --mode paper` and LIVE strategy `scan/run` are intentionally disabled and fail closed. Existing PAPER ledger/status/reset/resolution mechanics remain available. Historical target/legacy architecture sections below do not imply that automated trading is currently enabled.",
    "> **Current #48B activation status:** RESEARCH and durable PAPER strategy scanning share the same calibrated probability boundary. PAPER always recovers its durable ledger before calibration loading, but new model-backed decisions fail closed until an accepted artifact and provable exact-run forecast evidence exist. LIVE strategy `scan/run` remains disabled. Historical target/legacy architecture sections below do not imply that automated live trading is currently enabled.",
)
replace_once(
    "README.md",
    "uv run --no-dev python bot_v3.py scan --mode research\nuv run --no-dev python bot_v3.py status --mode paper\nuv run --no-dev python -m weatherbot.resolution --help",
    "uv run --no-dev python bot_v3.py scan --mode research\nuv run --no-dev python bot_v3.py scan --mode paper\nuv run --no-dev python bot_v3.py status --mode paper\nuv run --no-dev python -m weatherbot.resolution --help",
)
replace_once(
    "README.md",
    "# PAPER strategy scan/run are intentionally disabled during #48A.\n# Administrative PAPER commands remain available:\npython bot_v3.py status --mode paper\npython bot_v3.py resolve --mode paper",
    "# Durable PAPER uses the same calibrated probability boundary. Before final\n# #49/#48 activation this may intentionally create zero new model-backed entries.\npython bot_v3.py scan --mode paper\npython bot_v3.py run --mode paper\npython bot_v3.py status --mode paper\npython bot_v3.py resolve --mode paper",
)
replace_once(
    "README.md",
    "During #48A the public strategy entrypoint is RESEARCH-only; PAPER/LIVE strategy execution remains explicitly disabled until the later integration gate.",
    "During #48B the public strategy entrypoint supports calibrated RESEARCH and durable PAPER simulation. Both remain fail-closed without accepted compatible calibration evidence; LIVE strategy execution remains explicitly disabled.",
)
replace_between(
    "README.md",
    "## 🧠 Core Math: Gaussian Bucket Model",
    "## 🌀 Auto-Evolution Learning System",
    '''## 🧠 Core Math: Calibrated Residual Model

### Step 1 — Model Probability from ECMWF

The public strategy scanner no longer applies a global `sigma = 2°F`. It loads one separately accepted, checksummed calibration artifact and evaluates the residual distribution selected for the candidate's city/region, forecast source, lead, and season. Sparse groups fall back through the documented hierarchy; insufficient or incompatible evidence rejects the candidate.

For a forecast `f`, calibration models:

```text
R = observed_daily_high - forecast_daily_high
```

The probability of a `TemperatureBucket` is computed from the fitted residual CDF at the shared half-degree bucket boundaries. A normal runtime group may contain bias/sigma parameters internally, but `sigma` is not the scanner API.

Every probability carries immutable provenance including the model version, artifact SHA-256, city/region/lead inputs, selected group/fallback level, distribution type, sample count, training cutoff, and `model_probability`.

### Step 2 — Expected Value (EV)

```python
def calc_ev(model_probability, market_price):
    """Illustrative EV calculation from a model probability and market price."""
    win = model_probability * (1 / market_price - 1)
    lose = (1 - model_probability) * 1
    return win - lose
```

A positive model edge is not evidence of profitability. RESEARCH/PAPER decisions also pass freshness, executable-depth, cost, bankroll-sizing, and portfolio-risk gates.

### Step 3 — PAPER Sizing

PAPER does not use the legacy scanner's transient Kelly/`MAX_BET` sizing path. Its calibrated probability flows into the durable #15 bankroll-sizing and #16 portfolio-risk contracts, which reprice against executable order-book depth and enforce the configured per-trade and portfolio limits.

---

''',
)
replace_once(
    "README.md",
    "    ├── Gaussian Bucket Model → True Probability",
    "    ├── Calibrated residual model → Model probability",
)
replace_once(
    "README.md",
    "3. Gaussian model → true probability (σ=2°F)",
    "3. Accepted calibrated residual model → model probability (fail closed if unavailable/incompatible)",
)

replace_once(
    "docs/forecast-calibration.md",
    "model_version\nartifact_sha256\nforecast_source\ncalibration_group_key\nfallback_level\ndistribution_type\ncalibration_sample_count\ntraining_cutoff\nmodel_probability",
    "model_version\nartifact_sha256\ncity_slug\nclimate_region\nlead_days\nforecast_source\ncalibration_group_key\nfallback_level\ndistribution_type\ncalibration_sample_count\ntraining_cutoff\nmodel_probability",
)
replace_between(
    "docs/forecast-calibration.md",
    "Issue #48A intentionally activates only the research probability boundary.",
    "## Scanner-facing probability object",
    '''Issue #48B integrates both non-LIVE strategy modes through the same calibrated probability boundary while preserving separate activation gates:

```text
RESEARCH strategy probability  = calibrated artifact when separately accepted and runtime-compatible
PAPER strategy scanning         = same calibrated probability boundary + durable PAPER sizing/risk/ledger
LIVE strategy scanning          = disabled
fixed 2°F fallback              = absent
```

PAPER recovery runs before calibration loading so existing durable state can always be reconciled. A missing/corrupt/unaccepted global artifact then aborts new PAPER strategy work before weather or market collection. Candidate-local calibration failures reject only that candidate.

PAPER decision identity includes a canonical fingerprint of the exact `CalibratedProbability`, including artifact SHA and the probability input/group provenance. The scanner facade does not accept independent model-version or probability primitives, and caller audit metadata cannot override calibration-owned fields. Durable PAPER metadata retains the canonical mapping under `caller_audit.calibration` while the generic PAPER service remains model-agnostic.

Adding an accepted artifact still does not enable LIVE execution, and it does not weaken the forecast-vintage gate. The current stitched Open-Meteo forecast path cannot invent exact ECMWF run identity; candidates without provable compatible run provenance remain fail-closed.

''',
)
replace_once(
    "docs/forecast-calibration.md",
    "The resulting object carries `model_probability` together with the model version,\nartifact SHA-256, forecast source, selected calibration group and fallback level,\ndistribution type, sample count, and training cutoff. Research output persists these\nfields as a unit rather than storing a probability without its model identity.",
    "The resulting object carries `model_probability` together with the model version, artifact SHA-256, `city_slug`, `climate_region`, `lead_days`, forecast source, selected calibration group and fallback level, distribution type, sample count, and training cutoff. RESEARCH output and durable PAPER caller audit metadata persist this canonical mapping as a unit rather than storing a probability without its replay identity.",
)
replace_once(
    "docs/forecast-calibration.md",
    "### Runtime lead-domain and RESEARCH evidence",
    "### Runtime lead-domain and durable non-LIVE evidence",
)
replace_once(
    "docs/forecast-calibration.md",
    "The frozen calibration dataset supports only D+0, D+1, and D+2. Runtime probability evaluation rejects any other lead instead of falling through to a broader group trained on a different lead domain. The RESEARCH scanner requests exactly those supported horizons.",
    "The frozen calibration dataset supports only D+0, D+1, and D+2. Runtime probability evaluation rejects any other lead instead of falling through to a broader group trained on a different lead domain. The shared RESEARCH/PAPER scanner requests exactly those supported horizons.",
)
replace_once(
    "docs/forecast-calibration.md",
    "Continuous RESEARCH mode probes on the decision-window cadence so each U.S. timezone can be evaluated near its own market-local decision point; mechanical resolution monitoring continues between probes.",
    "Continuous RESEARCH and PAPER modes probe on the decision-window cadence so each U.S. timezone can be evaluated near its own market-local decision point; mechanical resolution monitoring continues independently between probes.",
)
replace_once(
    "docs/forecast-calibration.md",
    "Continuous RESEARCH does not sleep for an entire decision window between probes. It probes at most once per minute (and therefore at least four times faster than the 10-minute eligibility window) while scheduling resolution monitoring independently. This prevents monitor sleep/overhead from skipping a city's daily decision window.",
    "Continuous RESEARCH/PAPER does not sleep for an entire decision window between probes. It probes at most once per minute (and therefore at least four times faster than the 10-minute eligibility window) while scheduling resolution monitoring independently. This prevents monitor sleep/overhead from skipping a city's daily decision window.",
)
