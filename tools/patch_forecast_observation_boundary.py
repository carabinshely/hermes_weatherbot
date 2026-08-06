from __future__ import annotations

from pathlib import Path

path = Path("bot_v3.py")
content = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, *, label: str) -> None:
    global content
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one marker, found {count}")
    content = content.replace(old, new, 1)


import_marker = "from weatherbot.resolution import run_resolution_cycle as resolve_ledger_positions\n"
forecasting_import = '''from weatherbot.forecasting import (
    WeatherInputError,
    WeatherInputSnapshot,
    parse_aviation_weather_metar,
    parse_open_meteo_daily_highs,
)
'''
if forecasting_import not in content:
    replace_once(
        import_marker,
        forecasting_import + import_marker,
        label="forecasting import",
    )

start = content.index("def get_ecmwf(city_slug, dates):")
end_marker = "\n\n# =============================================================================\n# POLYMARKET\n"
end = content.index(end_marker, start)
new_weather_functions = '''def get_ecmwf(city_slug, dates):
    """ECMWF daily-high forecasts via Open-Meteo, with point-in-time provenance."""
    loc = LOCATIONS[city_slug]
    market_timezone = TIMEZONES[city_slug]
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=7&timezone={market_timezone}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    try:
        requested_dates = [datetime.strptime(value, "%Y-%m-%d").date() for value in dates]
    except ValueError as exc:
        raise WeatherInputError("forecast dates must use YYYY-MM-DD") from exc

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(5, 10))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise WeatherInputError("Open-Meteo response must be an object")
            retrieved_at = datetime.now(timezone.utc)
            forecasts = parse_open_meteo_daily_highs(
                payload,
                requested_dates=requested_dates,
                market_timezone=market_timezone,
                retrieved_at_utc=retrieved_at,
            )
            return {market_date.isoformat(): forecast for market_date, forecast in forecasts.items()}
        except (requests.RequestException, ValueError, WeatherInputError) as exc:
            if attempt < 2:
                time.sleep(2)
            else:
                warn(f"ECMWF error for {city_slug}: {exc}")
    return {}


def get_metar(city_slug):
    """Latest instantaneous METAR observation; never a daily-high forecast."""
    loc = LOCATIONS[city_slug]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={loc['station']}&format=json"
        response = requests.get(url, timeout=(5, 8))
        response.raise_for_status()
        payload = response.json()
        return parse_aviation_weather_metar(
            payload,
            station_id=loc["station"],
            market_timezone=TIMEZONES[city_slug],
            retrieved_at_utc=datetime.now(timezone.utc),
        )
    except (requests.RequestException, ValueError, WeatherInputError) as exc:
        warn(f"METAR error for {city_slug}: {exc}")
        return None


def get_forecast_snapshot(city_slug, dates):
    """Keep daily-high forecasts and current observations as separate typed data."""
    snapshot_started_at = datetime.now(timezone.utc)
    market_timezone = TIMEZONES[city_slug]
    calendar = MarketCalendar(market_timezone)
    forecasts = get_ecmwf(city_slug, dates)
    today = calendar.local_date(snapshot_started_at)
    observation = get_metar(city_slug) if today.isoformat() in dates else None

    result = {}
    for market_date, forecast in forecasts.items():
        matching_observation = (
            observation
            if observation is not None and observation.market_date == forecast.market_date
            else None
        )
        result[market_date] = WeatherInputSnapshot(
            forecast=forecast,
            observation=matching_observation,
            assembled_at_utc=datetime.now(timezone.utc),
        )
    return result
'''
content = content[:start] + new_weather_functions + content[end:]

replace_once(
    '''            forecastsnap = forecasts.get(market_date)
            if not forecastsnap:
                continue
            if (
                forecastsnap.get("market_date") != market_date
                or forecastsnap.get("market_timezone") != market_timezone
            ):
                errors.append(f"{loc['name']} {horizon}: unqualified forecast date")
                continue
            forecast_temp = forecastsnap.get("temp")
            best_source = forecastsnap.get("source", "ecmwf")
            if forecast_temp is None:
                continue
''',
    '''            weathersnap = forecasts.get(market_date)
            if weathersnap is None:
                continue
            if (
                weathersnap.forecast.market_date.isoformat() != market_date
                or weathersnap.forecast.market_timezone != market_timezone
            ):
                errors.append(f"{loc['name']} {horizon}: unqualified forecast date")
                continue
            forecast_temp = float(weathersnap.signal_temperature_f)
            best_source = weathersnap.forecast.source.value
''',
    label="scanner weather snapshot",
)

replace_once(
    '''            cost = float(quote.total_cost)
            shares = float(quote.shares)
            best_signal = {
''',
    '''            cost = float(quote.total_cost)
            shares = float(quote.shares)
            signal_generated_at = datetime.now(timezone.utc)
            weather_metadata = weathersnap.signal_metadata(
                generated_at_utc=signal_generated_at,
            )
            best_signal = {
''',
    label="signal weather metadata setup",
)

replace_once(
    '                "forecast_retrieved_at_utc": forecastsnap["retrieved_at_utc"],\n',
    '                **weather_metadata,\n',
    label="signal weather metadata fields",
)
replace_once(
    '                "signal_generated_at_utc": datetime.now(timezone.utc).isoformat(),\n',
    '                "signal_generated_at_utc": signal_generated_at.isoformat(),\n',
    label="signal generation timestamp",
)

replace_once(
    '''            print(
                f"  {C.CYAN}  Forecast: {forecast_temp}°F ({best_source}) | "
                f"{target_bucket.label}{C.RESET}"
            )
''',
    '''            print(
                f"  {C.CYAN}  Forecast high: {forecast_temp}°F ({best_source}) | "
                f"{target_bucket.label}{C.RESET}"
            )
            if weathersnap.observation is not None:
                observation = weathersnap.observation
                print(
                    f"  {C.GRAY}  Observation: {float(observation.temperature_f):.1f}°F "
                    f"METAR {observation.station_id} at "
                    f"{observation.valid_at_utc.isoformat()}{C.RESET}"
                )
''',
    label="weather console output",
)

if "forecastsnap" in content:
    raise SystemExit("legacy forecastsnap access remains after patch")
if "best = metar" in content or 'best_source = "metar"' in content:
    raise SystemExit("METAR forecast substitution remains after patch")

path.write_text(content, encoding="utf-8")
