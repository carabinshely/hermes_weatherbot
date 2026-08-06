from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "bot_v3.py",
    '''from weatherbot.forecasting import (
    WeatherInputError,
    WeatherInputSnapshot,
    parse_aviation_weather_metar,
    parse_open_meteo_daily_highs,
)
from weatherbot.resolution import run_resolution_cycle as resolve_ledger_positions
''',
    '''from weatherbot.forecasting import (
    WeatherInputError,
    WeatherInputSnapshot,
    parse_aviation_weather_metar,
    parse_open_meteo_daily_highs,
)
from weatherbot.quoting import (
    BalanceSnapshot,
    CostPolicy,
    DepthPolicy,
    FreshnessPolicy,
    MarketEventSnapshot,
    evaluate_executable_buy,
    revalidate_executable_buy,
)
from weatherbot.resolution import run_resolution_cycle as resolve_ledger_positions
''',
)
replace_once(
    "bot_v3.py",
    '''MAX_SLIPPAGE = _cfg.get("max_slippage", 0.03)
SCAN_INTERVAL = _cfg.get("scan_interval", 3600)
''',
    '''MAX_SLIPPAGE = _cfg.get("max_slippage", 0.03)
MAX_WORST_SLIPPAGE = _cfg.get("max_worst_slippage", 0.05)
MAX_FORECAST_AGE_SECONDS = _cfg.get("max_forecast_age_seconds", 21600)
MAX_EVENT_AGE_SECONDS = _cfg.get("max_event_age_seconds", 120)
MAX_ORDER_BOOK_AGE_SECONDS = _cfg.get("max_order_book_age_seconds", 30)
MAX_BALANCE_AGE_SECONDS = _cfg.get("max_balance_age_seconds", 30)
PLATFORM_FEE_RESERVE_RATE = _cfg.get("platform_fee_reserve_rate", 0.01)
TRANSACTION_COST_RESERVE = _cfg.get("transaction_cost_reserve", 0.01)
EXECUTION_SAFETY_MARGIN_RATE = _cfg.get("execution_safety_margin_rate", 0.02)
QUOTE_DEPTH_POLICY = DepthPolicy(str(_cfg.get("depth_policy", "reject")))
SCAN_INTERVAL = _cfg.get("scan_interval", 3600)
''',
)
replace_once(
    "bot_v3.py",
    '''def _fetch_selected_order_book(selection, *, now):
    """Fetch and validate the order book for one selected outcome token."""
''',
    '''def _fetch_selected_order_book(selection):
    """Fetch the selected token book; point-in-time freshness is checked centrally."""
''',
)
replace_once(
    "bot_v3.py",
    '''        expected_condition_id=selection.condition_id,
        expected_token_id=selection.token_id,
        now=now,
        maximum_age=timedelta(minutes=2),
    )
''',
    '''        expected_condition_id=selection.condition_id,
        expected_token_id=selection.token_id,
    )
''',
)
replace_once(
    "bot_v3.py",
    '''def _parse_temperature_markets(event):
''',
    '''def _parse_api_datetime(value, *, label):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GammaMarketError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GammaMarketError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GammaMarketError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _quote_freshness_policy():
    return FreshnessPolicy(
        maximum_forecast_age=timedelta(seconds=float(MAX_FORECAST_AGE_SECONDS)),
        maximum_event_age=timedelta(seconds=float(MAX_EVENT_AGE_SECONDS)),
        maximum_order_book_age=timedelta(seconds=float(MAX_ORDER_BOOK_AGE_SECONDS)),
        maximum_balance_age=timedelta(seconds=float(MAX_BALANCE_AGE_SECONDS)),
    )


def _quote_cost_policy():
    return CostPolicy(
        platform_fee_rate=Decimal(str(PLATFORM_FEE_RESERVE_RATE)),
        transaction_cost=Decimal(str(TRANSACTION_COST_RESERVE)),
        safety_margin_rate=Decimal(str(EXECUTION_SAFETY_MARGIN_RATE)),
        maximum_average_slippage=Decimal(str(MAX_SLIPPAGE)),
        maximum_worst_slippage=Decimal(str(MAX_WORST_SLIPPAGE)),
        maximum_all_in_price=Decimal(str(MAX_PRICE)),
        minimum_expected_return=Decimal(str(get_adjusted_ev_floor())),
        depth_policy=QUOTE_DEPTH_POLICY,
    )


def _quote_rejection_message(city_name, horizon, evaluation):
    reason = evaluation.rejection_reason
    reason_text = reason.value if reason is not None else "unknown"
    return f"{city_name} {horizon}: {reason_text}: {evaluation.detail}"


def _parse_temperature_markets(event):
''',
)
replace_once(
    "bot_v3.py",
    '''                event = get_polymarket_event(
                    city_slug,
                    MONTHS[parsed_date.month - 1],
                    parsed_date.day,
                    parsed_date.year,
                )
''',
    '''                event = get_polymarket_event(
                    city_slug,
                    MONTHS[parsed_date.month - 1],
                    parsed_date.day,
                    parsed_date.year,
                )
                event_retrieved_at = datetime.now(timezone.utc)
''',
)
replace_once(
    "bot_v3.py",
    '''            if not event:
                continue

            end_date = event.get("endDate", "")
''',
    '''            if not event:
                continue
            try:
                event_snapshot = MarketEventSnapshot(
                    event_id=str(event.get("id") or event.get("slug") or market_date),
                    retrieved_at_utc=event_retrieved_at,
                    source_updated_at_utc=_parse_api_datetime(
                        event.get("updatedAt"),
                        label="event.updatedAt",
                    ),
                )
            except (GammaMarketError, ValueError) as exc:
                errors.append(f"{loc['name']} {horizon_index}: {exc}")
                continue

            end_date = event.get("endDate", "")
''',
)
replace_once(
    "bot_v3.py",
    '''                selection = market.select(BinaryOutcome.YES)
                book = _fetch_selected_order_book(selection, now=datetime.now(timezone.utc))
''',
    '''                selection = market.select(BinaryOutcome.YES)
                book = _fetch_selected_order_book(selection)
''',
)
replace_once(
    "bot_v3.py",
    '''            volume = selected["volume"]
            if volume < MIN_VOLUME:
                continue
            if float(book.spread) > MAX_SLIPPAGE:
                continue
            if float(book.best_ask) >= MAX_PRICE:
                continue

            sigma = get_sigma(city_slug)
            probability = target_bucket.probability(forecast_temp, sigma)
            preliminary_price = float(book.best_ask)
            preliminary_ev = calc_ev(probability, preliminary_price)
            preliminary_kelly = get_adjusted_kelly(calc_kelly(probability, preliminary_price))
            if preliminary_ev < get_adjusted_ev_floor():
                continue
            size = bet_size(preliminary_kelly)
            if size < 0.50:
                continue

            try:
                quote = book.quote_buy_budget(Decimal(str(size)))
            except OrderBookError as exc:
                errors.append(f"{loc['name']} {horizon}: {exc}")
                continue

            entry_price = float(quote.average_price)
            execution_slippage = float(quote.worst_price - quote.best_ask)
            if entry_price >= MAX_PRICE or execution_slippage > MAX_SLIPPAGE:
                continue
            ev = calc_ev(probability, entry_price)
            adjusted_kelly = get_adjusted_kelly(calc_kelly(probability, entry_price))
            if ev < get_adjusted_ev_floor():
                continue

            cost = float(quote.total_cost)
            shares = float(quote.shares)
''',
    '''            volume = selected["volume"]
            if volume < MIN_VOLUME:
                continue

            sigma = get_sigma(city_slug)
            probability = target_bucket.probability(forecast_temp, sigma)
            preliminary_kelly = get_adjusted_kelly(
                calc_kelly(probability, float(book.best_ask))
            )
            size = bet_size(preliminary_kelly)
            if size < 0.50:
                continue

            balance_snapshot = None
            if is_live:
                refreshed_balance = get_usdc_balance(WALLET)
                balance = refreshed_balance
                balance_snapshot = BalanceSnapshot(
                    available_cash=Decimal(str(refreshed_balance)),
                    reserved_cash=Decimal("0"),
                    observed_at_utc=datetime.now(timezone.utc),
                    source="polygon-usdc-balance",
                )

            evaluation = evaluate_executable_buy(
                probability=Decimal(str(probability)),
                requested_budget=Decimal(str(size)),
                weather=weathersnap,
                event=event_snapshot,
                order_book=book,
                balance=balance_snapshot,
                evaluated_at=datetime.now(timezone.utc),
                freshness_policy=_quote_freshness_policy(),
                cost_policy=_quote_cost_policy(),
            )
            if not evaluation.accepted:
                message = _quote_rejection_message(loc["name"], horizon, evaluation)
                errors.append(message)
                warn(f"  quote rejected: {message}")
                continue
            validated_quote = evaluation.quote
            assert validated_quote is not None

            if is_live:
                try:
                    refreshed_book = _fetch_selected_order_book(selection)
                    refreshed_balance = get_usdc_balance(WALLET)
                    balance = refreshed_balance
                    refreshed_balance_snapshot = BalanceSnapshot(
                        available_cash=Decimal(str(refreshed_balance)),
                        reserved_cash=Decimal("0"),
                        observed_at_utc=datetime.now(timezone.utc),
                        source="polygon-usdc-balance",
                    )
                    revalidated = revalidate_executable_buy(
                        validated_quote,
                        probability=Decimal(str(probability)),
                        requested_budget=Decimal(str(size)),
                        weather=weathersnap,
                        event=event_snapshot,
                        order_book=refreshed_book,
                        balance=refreshed_balance_snapshot,
                        evaluated_at=datetime.now(timezone.utc),
                        freshness_policy=_quote_freshness_policy(),
                        cost_policy=_quote_cost_policy(),
                    )
                except (OrderBookError, requests.RequestException, ValueError) as exc:
                    errors.append(f"{loc['name']} {horizon}: revalidation failed: {exc}")
                    continue
                if not revalidated.accepted:
                    message = _quote_rejection_message(loc["name"], horizon, revalidated)
                    errors.append(message)
                    warn(f"  refreshed quote rejected: {message}")
                    continue
                validated_quote = revalidated.quote
                assert validated_quote is not None
                book = refreshed_book

            quote = validated_quote.quote
            entry_price = float(quote.average_price)
            all_in_price = float(validated_quote.all_in_average_price)
            execution_slippage = float(validated_quote.worst_slippage)
            ev = float(validated_quote.expected_return)
            adjusted_kelly = get_adjusted_kelly(calc_kelly(probability, all_in_price))
            cost = float(validated_quote.total_all_in_cost)
            book_cost = float(quote.total_cost)
            shares = float(quote.shares)
''',
)
replace_once(
    "bot_v3.py",
    '''                "entry_price": entry_price,
                "best_bid": float(quote.best_bid),
''',
    '''                "entry_price": entry_price,
                "all_in_price": all_in_price,
                "best_bid": float(quote.best_bid),
''',
)
replace_once(
    "bot_v3.py",
    '''                "shares": shares,
                "cost": cost,
                "p": round(probability, 6),
''',
    '''                "shares": shares,
                "book_cost": book_cost,
                "cost": cost,
                "platform_fee_reserve": float(validated_quote.platform_fee),
                "transaction_cost_reserve": float(validated_quote.transaction_cost),
                "safety_margin_reserve": float(validated_quote.safety_margin),
                "probability_edge": float(validated_quote.probability_edge),
                "p": round(probability, 6),
''',
)
replace_once(
    "bot_v3.py",
    '''                "sigma": sigma,
                "volume": volume,
            }
''',
    '''                "sigma": sigma,
                "volume": volume,
                "event_retrieved_at_utc": event_snapshot.retrieved_at_utc.isoformat(),
                "event_source_updated_at_utc": (
                    None
                    if event_snapshot.source_updated_at_utc is None
                    else event_snapshot.source_updated_at_utc.isoformat()
                ),
                **validated_quote.metadata(),
            }
''',
)
replace_once(
    "bot_v3.py",
    '''                    "price": entry_price,
''',
    '''                    "price": all_in_price,
''',
)
replace_once(
    "bot_v3.py",
    '''                f"  {C.GREEN}  ✅ BUY SIGNAL | ${cost:.2f} @ ${entry_price:.3f} | "
                f"EV {ev:+.2f} | Kel {adjusted_kelly:.2f}{C.RESET}"
''',
    '''                f"  {C.GREEN}  ✅ BUY SIGNAL | all-in ${cost:.2f} "
                f"(book ${book_cost:.2f}) @ ${entry_price:.3f} "
                f"[all-in ${all_in_price:.3f}] | net EV {ev:+.2f} | "
                f"Kel {adjusted_kelly:.2f}{C.RESET}"
''',
)
replace_once(
    "bot_v3.py",
    '''                    price=best_signal["entry_price"],
''',
    '''                    price=best_signal["worst_price"],
''',
)

config = Path("config.json")
config.write_text(
    '''{
  "balance": 0.0,
  "max_bet": 2.0,
  "min_ev": 0.1,
  "max_price": 0.45,
  "min_volume": 500,
  "min_hours": 2.0,
  "max_hours": 72.0,
  "kelly_fraction": 0.25,
  "scan_interval": 3600,
  "calibration_min": 30,
  "mode": "research",
  "max_slippage": 0.03,
  "max_worst_slippage": 0.05,
  "max_forecast_age_seconds": 21600,
  "max_event_age_seconds": 120,
  "max_order_book_age_seconds": 30,
  "max_balance_age_seconds": 30,
  "platform_fee_reserve_rate": 0.01,
  "transaction_cost_reserve": 0.01,
  "execution_safety_margin_rate": 0.02,
  "depth_policy": "reject"
}
''',
    encoding="utf-8",
)
