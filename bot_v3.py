#!/usr/bin/env python3
"""WeatherBot v3 calibrated RESEARCH and PAPER entrypoint.

The pre-calibration implementation is retained in ``bot_v3_legacy.py`` only for
administrative/mechanical compatibility. Strategy scanning requires a separately approved
calibration artifact. RESEARCH and PAPER share one calibrated probability boundary; LIVE
strategy scanning remains explicitly disabled.
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
from weatherbot.domain import MarketId, OutcomeId, RiskScope
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
from weatherbot.paper import (
    PaperEntryStatus,
    recover_paper_runtime,
    submit_scanner_candidate,
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
    context: ExecutionContext,
    errors: int,
    observed_signals: int,
    top_signals: list[dict[str, object]],
) -> None:
    """Send a non-LIVE summary using model-probability terminology."""
    status_emoji = "✅" if errors == 0 else "⚠️"
    count_label = "PAPER fills" if context.mode is ExecutionMode.PAPER else "Signals observed"
    lines = [
        f"🔒 <b>{context.label} MODE</b>",
        "🔔 <b>Weather Bot — Scan Report</b>",
        f"{status_emoji} Cities: {len(LOCATIONS)} | Errors: {errors}",
        f"🔎 {count_label}: {observed_signals}",
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
    message = f"{context.label} strategy scanning remains disabled under #48"
    _legacy.warn(message)
    return 0, [message]


def scan_and_trade(context: ExecutionContext):
    """Evaluate calibrated weather-market signals in RESEARCH or durable PAPER mode."""
    if context.mode is ExecutionMode.LIVE:
        return _blocked_strategy_scan(context)
    if context.mode not in {ExecutionMode.RESEARCH, ExecutionMode.PAPER}:
        return _blocked_strategy_scan(context)

    if context.mode is ExecutionMode.PAPER:
        try:
            recover_paper_runtime(runtime=PAPER_RUNTIME)
        except Exception as exc:
            message = f"PAPER recovery failed closed before strategy scanning: {exc}"
            _legacy.warn(message)
            return 0, [message]

    try:
        calibration_runtime = load_calibrated_probability_runtime(repository_root=BOT_DIR)
    except CalibrationRuntimeError as exc:
        message = f"calibration unavailable; {context.label} scan failed closed: {exc}"
        _legacy.warn(message)
        return 0, [message]

    now = datetime.now(UTC)
    errors: list[str] = []
    new_trades = 0
    observed_signals = 0
    top_signals: list[dict[str, object]] = []

    print(f"\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — {context.label} MODE{C.RESET}")
    print("=" * 60)
    print("  Wallet access: disabled")
    if context.mode is ExecutionMode.PAPER:
        print(f"  Paper ledger: {PAPER_RUNTIME.ledger_path}")
        print("  Sizing: durable bankroll + portfolio-risk policy")
    else:
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

            if context.mode is ExecutionMode.PAPER:
                evaluated_at = datetime.now(UTC)
                paper_scope = RiskScope(
                    market_id=MarketId(str(selection.market_id)),
                    outcome_id=OutcomeId(str(selection.token_id)),
                    event_id=event_snapshot.event_id,
                    city_key=city_slug,
                    market_date=weather.forecast.market_date,
                )
                try:
                    paper_result = submit_scanner_candidate(
                        runtime=PAPER_RUNTIME,
                        strategy_id="bot-v3-weather",
                        calibrated=calibrated,
                        scope=paper_scope,
                        weather=weather,
                        event=event_snapshot,
                        decision_book=book,
                        condition_id=selection.condition_id,
                        token_id=selection.token_id,
                        evaluated_at=evaluated_at,
                        freshness_policy=_legacy._quote_freshness_policy(),
                        cost_policy=_legacy._quote_cost_policy(),
                        fetch_book=_legacy._fetch_token_order_book,
                        audit_metadata={
                            "city_name": loc["name"],
                            "horizon": horizon,
                            "bucket_key": target_bucket.key,
                            "bucket_label": target_bucket.label,
                            "forecast_temperature_f": weather.signal_temperature_f,
                            "volume": selected["volume"],
                            "question": market.question,
                            "declared_resolution_source": market.resolution_source,
                            "event_end_date": end_date,
                        },
                        owner_id=f"paper-scanner:{city_slug}:{market_date}",
                    )
                except (OrderBookError, requests.RequestException, ValueError) as exc:
                    message = f"{loc['name']} {horizon}: PAPER execution failed: {exc}"
                    errors.append(message)
                    _legacy.warn(f"  {message}")
                    continue

                plan = paper_result.execution_plan
                if paper_result.status in {
                    PaperEntryStatus.FILLED,
                    PaperEntryStatus.PARTIAL_FILL,
                }:
                    assert plan is not None
                    assert plan.average_price is not None
                    new_trades += 1
                    observed_signals += 1
                    city_found_signal = True
                    paper_cost = float((plan.gross_value + plan.fee).amount)
                    paper_price = float(plan.average_price)
                    paper_ev = 0.0
                    if paper_result.sizing is not None and paper_result.sizing.quote is not None:
                        paper_ev = float(paper_result.sizing.quote.expected_return)
                    top_signals.append(
                        {
                            "city": loc["name"],
                            "horizon": horizon,
                            "bucket": target_bucket.label,
                            "ev": paper_ev,
                            "price": paper_price,
                            "model_probability": probability,
                            "provenance": calibrated.audit_metadata(),
                        }
                    )
                    status_label = (
                        "PAPER FILLED"
                        if paper_result.status is PaperEntryStatus.FILLED
                        else "PAPER PARTIAL FILL"
                    )
                    _legacy.info(
                        f"  [{status_label}] {target_bucket.label} | "
                        f"{plan.filled_quantity}/{plan.requested_quantity} @ ${paper_price:.3f} | "
                        f"model probability {probability:.3f} | EV {paper_ev:+.2f} | "
                        f"simulated all-in ${paper_cost:.2f} | "
                        f"artifact {calibrated.artifact_sha256[:12]}"
                    )
                elif paper_result.status is PaperEntryStatus.IDEMPOTENT:
                    _legacy.info("  [PAPER] durable calibrated decision already processed")
                else:
                    reason = paper_result.status.value
                    if paper_result.risk_decision is not None:
                        rejection = paper_result.risk_decision.rejection_reason
                        reason = rejection.value if rejection is not None else reason
                    elif (
                        paper_result.sizing is not None
                        and paper_result.sizing.rejection_reason is not None
                    ):
                        reason = paper_result.sizing.rejection_reason.value
                    elif plan is not None:
                        reason = plan.reason
                    _legacy.info(f"  [PAPER] rejected: {reason}")
                continue

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
        context=context,
        errors=len(errors),
        observed_signals=observed_signals,
        top_signals=top_signals,
    )
    return new_trades, errors


def show_status(context: ExecutionContext):
    """Preserve administrative PAPER status independently of strategy calibration."""
    _legacy.PAPER_RUNTIME = PAPER_RUNTIME
    return _legacy.show_status(context)


def run_resolution_monitor_cycle(ledger_path=None):
    """Delegate resolution monitoring without exposing the quarantined legacy scanner."""
    if ledger_path is None:
        ledger_path = _legacy.LEDGER_PATH
    return _legacy.run_resolution_monitor_cycle(ledger_path)


def run_loop(context: ExecutionContext):
    """Run calibrated RESEARCH/PAPER scans while retaining mechanical resolution monitoring."""
    if context.mode is ExecutionMode.LIVE:
        return _blocked_strategy_scan(context)
    if context.mode not in {ExecutionMode.RESEARCH, ExecutionMode.PAPER}:
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
                resolution_ledger = (
                    PAPER_RUNTIME.ledger_path if context.mode is ExecutionMode.PAPER else None
                )
                run_resolution_monitor_cycle(resolution_ledger)
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
        if context.mode is ExecutionMode.LIVE:
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
