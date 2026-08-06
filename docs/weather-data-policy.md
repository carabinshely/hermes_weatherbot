# Weather input policy

The signal target is the **maximum temperature for one market-local calendar day**.
An instantaneous station observation is a different datum and must never be substituted
for that daily-high forecast.

## Deterministic source selection

| Market horizon | Daily-high signal input | Optional observation |
| --- | --- | --- |
| Current local day | Open-Meteo ECMWF IFS 0.25° `temperature_2m_max` | Latest matching airport METAR for the same local date |
| Future local day | Open-Meteo ECMWF IFS 0.25° `temperature_2m_max` | None |

The policy is deliberately the same in the morning, afternoon, and evening. METAR is
recorded for context and future nowcasting research, but it does not alter the signal
forecast. A nowcast may use observations only after a documented and calibrated model is
implemented; no such model exists today.

A missing, stale-date, or malformed METAR therefore has one effect: the observation fields
are absent. It cannot change the meaning or value of the forecast field. A missing daily
maximum forecast produces no signal; METAR cannot be used as a fallback.

## Point-in-time provenance

Every daily-high forecast records:

- source and model identifier;
- application snapshot issue time;
- optional upstream model-run initialization time;
- retrieval time;
- the exact UTC interval corresponding to the market-local day;
- age at signal generation.

Every METAR observation records:

- source and station identifier;
- report/issue time;
- observation valid time;
- provider receipt time when supplied;
- bot retrieval time;
- age at signal generation.

Signal records retain forecast and observation values under separate keys. The compatibility
fields `forecast_temp` and `forecast_src` continue to refer only to the daily-high forecast.

## Open-Meteo run-time limitation

The operational Open-Meteo forecast endpoint is a seamless, continuously updated series.
It does not identify the exact upstream model run that supplied each returned value. For
that endpoint, `forecast_snapshot_issued_at_utc` is the time the bot received and issued its
immutable application snapshot, while `forecast_model_run_initialized_at_utc` remains
`null` rather than being guessed.

Calibration and historical evaluation in #12 must use Open-Meteo's Single Runs API, whose
required `run` parameter identifies an exact UTC model initialization time:

- https://open-meteo.com/en/docs/ecmwf-api
- https://open-meteo.com/en/docs/single-runs-api

METAR field semantics and public JSON access are documented by Aviation Weather Center:

- https://aviationweather.gov/data/api/
- https://aviationweather.gov/help/data/
