#!/usr/bin/env python3
"""WeatherBot v3 calibrated RESEARCH entrypoint.

The pre-calibration implementation is retained in ``bot_v3_legacy.py`` only for
administrative/mechanical compatibility. Strategy scanning in this entrypoint is
RESEARCH-only and requires a separately approved calibration artifact. PAPER and LIVE
strategy scans remain explicitly disabled until the remaining #48 integration is reviewed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal

import requests

import bot_v3_legacy as _legacy
from execution_modes import (
    ExecutionContext,
    ExecutionMode,
    ModeConfigurationError,
    resolve_execution_context,
)
from weatherbot.forecasting import (
    CalibrationRuntimeError,
    load_calibrated_probability_runtime,
)
from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.contracts import (
    CALIBRATION_DECISION_WINDOW,
    CALIBRATION_LEAD_DAYS,
    calibration_runtime_window,
)
from weatherbot.markets import (
    BinaryOutcome,
    GammaMarketError,
    OrderBookError,
    TemperatureMarketError,
    TemperatureUnit,
)
from weatherbot.quoting import evaluate_executable_buy

_CLIMATE_REGIONS = {
    "nyc": "northeast",
    "chicago": "ohio_valley",
    "miami": "southeast",
    "dallas": "south",
    "seattle": "northwest",
    "atlanta": "southeast",
}

LOCATIONS = {
    city: {**details, "climate_region": _CLIMATE_REGIONS[city]}
    for city, details in _legacy.LOCATIONS.items()
}
TIMEZONES = _legacy.TIMEZONES
MONTHS = _legacy.MONTHS
BOT_DIR = _legacy.BOT_DIR
SCAN_INTERVAL = _legacy.SCAN_INTERVAL
MIN_VOLUME = _legacy.MIN_VOLUME
MIN_HOURS = _legacy.MIN_HOURS
MAX_HOURS = _legacy.MAX_HOURS
MAX_BET = _legacy.MAX_BET
MIN_EV = _legacy.MIN_EV
PAPER_RUNTIME = _legacy.PAPER_RUNTIME
C = _legacy.C
RESEARCH_SIGNAL_LOG = BOT_DIR / "state" / "research-signals.jsonl"


def persist_research_signal(signal: dict[str, object]) -> None:
    """Append one complete research signal with provenance to durable local history."""
    RESEARCH_SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        signal,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    with RESEARCH_SIGNAL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def tg_scan_summary(
    *,
    errors: int,
    observed_signals: int,
    top_signals: list[dict[str, object]],
) -> None:
    """Send a RESEARCH-only summary using model-probability terminology."""
    status_emoji = "✅" if errors == 0 else "⚠️"
    lines = [
        "🔒 <b>RESEARCH MODE</b>",
        "🔔 <b>Weather Bot — Scan Report</b>",
        f"{status_emoji} Cities: {len(LOCATIONS)} | Errors: {errors}",
        f"🔎 Signals observed: {observed_signals}",
        "💰 Wallet access: <b>disabled</b>",
    ]
    if top_signals:
        lines.extend(("", f"🎯 <b>Top EV Signals ({len(top_signals)} found):</b>"))
        for signal in top_signals[:5]:
            lines.append(
                f"  • {signal['city']} {signal['horizon']} | "
                f"{signal['bucket']} | EV <b>+{float(signal['ev']):.2f}</b> | "
                f"${float(signal['price']):.3f} (market) vs "
                f"{float(signal['model_probability']):.3f} (model probability)"
            )
    _legacy.send_telegram("\n".join(lines))


def _blocked_strategy_scan(context: ExecutionContext) -> tuple[int, list[str]]:
    message = (
        f"{context.label} strategy scanning is disabled until calibrated execution "
        "integration is explicitly reviewed under #48"
    )
    _legacy.warn(message)
    return 0, [message]


def scan_and_trade(context: ExecutionContext):
    """Evaluate calibrated weather-market signals in RESEARCH mode only."""
    if context.mode is not ExecutionMode.RESEARCH:
        return _blocked_strategy_scan(context)

    try:
        calibration_runtime = load_calibrated_probability_runtime(repository_root=BOT_DIR)
    except CalibrationRuntimeError as exc:
        message = f"calibration unavailable; RESEARCH scan failed closed: {exc}"
        _legacy.warn(message)
        return 0, [message]

    now = datetime.now(UTC)
    errors: list[str] = []
    observed_signals = 0
    top_signals: list[dict[str, object]] = []

    print(f"\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — RESEARCH MODE{C.RESET}")
    print("=" * 60)
    print("  Wallet access: disabled")
    print(f"  Max research sizing reference: ${MAX_BET} | Min EV: {MIN_EV * 100:.0f}%")
    print()

    for city_slug, loc in LOCATIONS.items():
        print(f"  -> {loc['name']}...", end=" ", flush=True)
        market_timezone = TIMEZONES[city_slug]
        calendar = _legacy.MarketCalendar(market_timezone)
        dates = tuple(
            candidate.isoformat()
            for candidate in calendar.candidate_dates(now, count=len(CALIBRATION_LEAD_DAYS))
        )
        try:
            decision_start, decision_end = calibration_runtime_window(
                target_date=datetime.strptime(dates[0], "%Y-%m-%d").date(),
                market_timezone=market_timezone,
                lead_days=CALIBRATION_LEAD_DAYS[0],
            )
        except (ValueError, IndexError) as exc:
            errors.append(f"{loc['name']}: invalid calibration decision window: {exc}")
            print("invalid decision window")
            continue
        if not decision_start <= now < decision_end:
            _legacy.skip(f"{loc['name']}: outside calibrated 00:15 market-local decision window")
            print("outside calibrated decision window")
            continue

        try:
            started = time.time()
            forecasts = _legacy.get_forecast_snapshot(city_slug, dates)
            _legacy.info(f"[{loc['name']}] forecast loaded in {time.time() - started:.1f}s")
            time.sleep(0.3)
        except Exception as exc:
            message = f"{loc['name']}: forecast failed: {exc}"
            errors.append(message)
            print(f"error ({exc})")
            continue

        city_found_signal = False
        for horizon_index, market_date in zip(CALIBRATION_LEAD_DAYS, dates, strict=True):
            horizon = f"D+{horizon_index}"
            try:
                parsed_date = datetime.strptime(market_date, "%Y-%m-%d")
                event = _legacy.get_polymarket_event(
                    city_slug,
                    MONTHS[parsed_date.month - 1],
                    parsed_date.day,
                    parsed_date.year,
                )
                event_retrieved_at = datetime.now(UTC)
            except Exception as exc:
                errors.append(f"{loc['name']} {horizon}: market lookup failed: {exc}")
                continue
            if not event:
                continue

            try:
                event_snapshot = _legacy.MarketEventSnapshot(
                    event_id=str(event.get("id") or event.get("slug") or market_date),
                    retrieved_at_utc=event_retrieved_at,
                    source_updated_at_utc=_legacy._parse_api_datetime(
                        event.get("updatedAt"), label="event.updatedAt"
                    ),
                )
            except (GammaMarketError, ValueError) as exc:
                errors.append(f"{loc['name']} {horizon}: {exc}")
                continue

            end_date = event.get("endDate", "")
            hours = _legacy.hours_to_resolution(end_date) if end_date else 0
            if hours < MIN_HOURS or hours > MAX_HOURS:
                continue

            weather = forecasts.get(market_date)
            if weather is None:
                continue
            if (
                weather.forecast.market_date.isoformat() != market_date
                or weather.forecast.market_timezone != market_timezone
            ):
                errors.append(f"{loc['name']} {horizon}: unqualified forecast date")
                continue
            forecast_temp = float(weather.signal_temperature_f)
            if forecast_temp < -40 or forecast_temp > 130:
                errors.append(f"{loc['name']} {horizon}: invalid forecast temperature")
                continue

            try:
                outcomes, partition = _legacy._parse_temperature_markets(event)
                if partition.unit is not TemperatureUnit.FAHRENHEIT:
                    raise TemperatureMarketError("US scanner expects Fahrenheit markets")
                target_bucket = partition.bucket_for_forecast(forecast_temp)
                matches = [item for item in outcomes if item["bucket"].key == target_bucket.key]
                if len(matches) != 1:
                    raise TemperatureMarketError(
                        f"forecast bucket {target_bucket.label} maps to {len(matches)} markets"
                    )
                selected = matches[0]
                market = selected["market"]
                selection = market.select(BinaryOutcome.YES)
                book = _legacy._fetch_selected_order_book(selection)
            except (
                GammaMarketError,
                TemperatureMarketError,
                OrderBookError,
                requests.RequestException,
            ) as exc:
                errors.append(f"{loc['name']} {horizon}: market rejected: {exc}")
                continue

            if selected["volume"] < MIN_VOLUME:
                continue

            try:
                calibrated = calibration_runtime.probability(
                    city=city_slug,
                    climate_region=str(loc["climate_region"]),
                    lead_days=horizon_index,
                    weather=weather,
                    bucket=target_bucket,
                )
            except (CalibrationError, CalibrationRuntimeError) as exc:
                _legacy.skip(f"{loc['name']} {horizon}: calibration rejected candidate: {exc}")
                continue

            probability = float(calibrated.model_probability)
            preliminary_kelly = _legacy.get_adjusted_kelly(
                _legacy.calc_kelly(probability, float(book.best_ask))
            )
            size = _legacy.bet_size(preliminary_kelly)
            if size < 0.50:
                continue

            evaluation = evaluate_executable_buy(
                probability=calibrated.model_probability,
                requested_budget=Decimal(str(size)),
                weather=weather,
                event=event_snapshot,
                order_book=book,
                balance=None,
                evaluated_at=datetime.now(UTC),
                freshness_policy=_legacy._quote_freshness_policy(),
                cost_policy=_legacy._quote_cost_policy(),
            )
            if not evaluation.accepted:
                errors.append(_legacy._quote_rejection_message(loc["name"], horizon, evaluation))
                continue
            validated_quote = evaluation.quote
            assert validated_quote is not None
            quote = validated_quote.quote
            all_in_price = float(validated_quote.all_in_average_price)
            ev = float(validated_quote.expected_return)
            signal_generated_at = datetime.now(UTC)
            weather_metadata = weather.signal_metadata(generated_at_utc=signal_generated_at)
            signal = {
                "market_id": str(selection.market_id),
                "condition_id": str(selection.condition_id),
                "outcome": selection.outcome.value,
                "token_id": str(selection.token_id),
                "question": market.question,
                "bucket_key": target_bucket.key,
                "bucket_label": target_bucket.label,
                "entry_price": float(quote.average_price),
                "all_in_price": all_in_price,
                "ev": round(ev, 4),
                "forecast_temp": forecast_temp,
                "city_slug": city_slug,
                "climate_region": str(loc["climate_region"]),
                "lead_days": horizon_index,
                "market_date": market_date,
                "market_timezone": market_timezone,
                "signal_generated_at_utc": signal_generated_at.isoformat(),
                "volume": selected["volume"],
                **weather_metadata,
                **calibrated.audit_metadata(),
                **validated_quote.metadata(),
            }
            try:
                persist_research_signal(signal)
            except (OSError, TypeError, ValueError) as exc:
                errors.append(f"{loc['name']} {horizon}: research signal persistence failed: {exc}")
                continue
            top_signals.append(
                {
                    "city": loc["name"],
                    "horizon": horizon,
                    "bucket": target_bucket.label,
                    "ev": ev,
                    "price": all_in_price,
                    "model_probability": probability,
                    "provenance": calibrated.audit_metadata(),
                }
            )
            city_found_signal = True
            observed_signals += 1
            _legacy.info(
                f"  [RESEARCH] {target_bucket.label} | market ${all_in_price:.3f} | "
                f"model probability {probability:.3f} | EV {ev:+.2f} | "
                f"artifact {calibrated.artifact_sha256[:12]}"
            )

        if not city_found_signal:
            print("ok", end="", flush=True)
        print()

    top_signals.sort(key=lambda item: float(item["ev"]), reverse=True)
    print(f"\n{'=' * 60}")
    print(f"  Scanned: {len(LOCATIONS)} cities")
    print(f"  Signals: {observed_signals}")
    print(f"  Errors:  {len(errors)}")
    print(f"{'=' * 60}\n")
    tg_scan_summary(
        errors=len(errors),
        observed_signals=observed_signals,
        top_signals=top_signals,
    )
    return 0, errors


def show_status(context: ExecutionContext):
    """Preserve administrative PAPER status while strategy scanning stays disabled."""
    _legacy.PAPER_RUNTIME = PAPER_RUNTIME
    return _legacy.show_status(context)


def run_resolution_monitor_cycle(ledger_path=None):
    """Delegate resolution monitoring without exposing the quarantined strategy scanner."""
    if ledger_path is None:
        ledger_path = _legacy.LEDGER_PATH
    return _legacy.run_resolution_monitor_cycle(ledger_path)


def run_loop(context: ExecutionContext):
    """Run calibrated RESEARCH scans while retaining mechanical resolution monitoring."""
    if context.mode is not ExecutionMode.RESEARCH:
        return _blocked_strategy_scan(context)

    last_scan_probe = 0.0
    last_resolution = 0.0
    scan_probe_interval = min(
        60.0,
        CALIBRATION_DECISION_WINDOW.total_seconds() / 4.0,
    )
    resolution_interval = max(1.0, float(_legacy.MONITOR_INTERVAL))
    sleep_interval = min(scan_probe_interval, resolution_interval)

    while True:
        now_ts = time.time()
        if now_ts - last_scan_probe >= scan_probe_interval:
            scan_and_trade(context)
            last_scan_probe = now_ts

        if now_ts - last_resolution >= resolution_interval:
            try:
                run_resolution_monitor_cycle()
            except Exception as exc:
                _legacy.warn(f"Resolution monitor error: {exc}")
            last_resolution = now_ts

        time.sleep(sleep_interval)


def main(argv: list[str] | None = None) -> int:
    parser = _legacy.build_parser()
    args = parser.parse_args(argv)
    try:
        context = resolve_execution_context(
            configured_mode=_legacy._cfg.get("mode"),
            cli_mode=args.mode,
            confirm_live=args.confirm_live,
        )
    except ModeConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command in {"scan", "run"}:
        print(f"Execution mode: {context.label}")
        if context.mode is not ExecutionMode.RESEARCH:
            _blocked_strategy_scan(context)
            return 2
        if args.command == "scan":
            scan_and_trade(context)
        else:
            run_loop(context)
        return 0

    # Administrative/mechanical commands retain the existing implementation. The
    # quarantined module is never delegated a strategy scan or run command here.
    _legacy.PAPER_RUNTIME = PAPER_RUNTIME
    return _legacy.main(argv)


if __name__ == "__main__":
    sys.exit(main())
