#!/usr/bin/env python3
"""Quarantined historical WeatherBot v3 implementation.

This module is retained only for internal PAPER compatibility and historical tests. The
supported public producer never imports it, and ``bot_v3_legacy.py`` blocks the legacy CLI.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from execution_modes import ExecutionContext, ExecutionMode, ModeConfigurationError, resolve_execution_context
from runtime_security import install_runtime_security
from weatherbot.dependencies import require_live_dependencies
from weatherbot.forecasting import WeatherInputError, WeatherInputSnapshot
from weatherbot.forecasting.providers import parse_aviation_weather_metar, parse_open_meteo_daily_highs
from weatherbot.markets import (
    BinaryOutcome,
    ConditionId,
    GammaMarketError,
    MarketCalendar,
    OrderBookError,
    OutcomeTokenId,
    TemperatureBucket,
    TemperatureMarketError,
    TemperatureMarketPartition,
    parse_gamma_binary_market,
    parse_order_book,
    parse_temperature_bucket,
)
from weatherbot.paper import PaperRuntimeConfig
from weatherbot.quoting import (
    CostPolicy,
    DepthPolicy,
    FreshnessPolicy,
    MarketEventSnapshot,
    ValidatedExecutableQuote,
    evaluate_executable_buy,
    revalidate_executable_buy,
)
from weatherbot.resolution import resolve_ledger_positions

load_dotenv()

BOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BOT_DIR / "config.json"
with CONFIG_PATH.open(encoding="utf-8") as handle:
    _cfg = json.load(handle)

install_runtime_security()


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"


def ok(message: str) -> None:
    print(f"{C.GREEN}✓{C.RESET} {message}")


def warn(message: str) -> None:
    print(f"{C.YELLOW}⚠{C.RESET} {message}")


def err(message: str) -> None:
    print(f"{C.RED}✗{C.RESET} {message}")


def live(message: str) -> None:
    print(f"{C.MAGENTA}● LIVE{C.RESET} {message}")


# --- Mode / credentials ---
PK = os.getenv("PK", "").strip()
WALLET = os.getenv("WALLET", "").strip()
SIG_TYPE = int(os.getenv("SIG_TYPE", "0"))

# --- Strategy config ---
BALANCE = float(_cfg.get("balance", 100.0))
MAX_BET = float(_cfg.get("max_bet", 2.0))
MIN_EV = float(_cfg.get("min_ev", 0.10))
MAX_PRICE = float(_cfg.get("max_price", 0.70))
KELLY_FRAC = float(_cfg.get("kelly_fraction", 0.25))
MIN_VOLUME = float(_cfg.get("min_volume", 500))
MIN_HOURS = float(_cfg.get("min_hours", 2))
MAX_HOURS = float(_cfg.get("max_hours", 72))
SCAN_INTERVAL = int(_cfg.get("scan_interval_seconds", 3600))
MAX_FORECAST_AGE_SECONDS = int(_cfg.get("max_forecast_age_seconds", 21600))
MAX_EVENT_AGE_SECONDS = int(_cfg.get("max_event_age_seconds", 120))
MAX_ORDER_BOOK_AGE_SECONDS = int(_cfg.get("max_order_book_age_seconds", 30))
MAX_BALANCE_AGE_SECONDS = int(_cfg.get("max_balance_age_seconds", 30))
PLATFORM_FEE_RESERVE_RATE = float(_cfg.get("platform_fee_reserve_rate", 0.01))
TRANSACTION_COST_RESERVE = float(_cfg.get("transaction_cost_reserve", 0.01))
MARKET_SAFETY_MARGIN_RATE = float(_cfg.get("market_safety_margin_rate", 0.02))
MAX_AVERAGE_SLIPPAGE = float(_cfg.get("max_average_slippage", 0.03))
MAX_WORST_SLIPPAGE = float(_cfg.get("max_worst_slippage", 0.05))
MAX_ALL_IN_PRICE = float(_cfg.get("max_all_in_price", 0.80))
QUOTE_DEPTH_POLICY = str(_cfg.get("quote_depth_policy", "reject"))
PAPER_RUNTIME = PaperRuntimeConfig.from_mapping(_cfg, base_dir=BOT_DIR)

# --- CLOB ---
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- Quarantined historical LIVE addresses ---
# These are deliberately not embedded in the repository. The supported public producer
# and internal PAPER runtime do not need them. Any direct legacy LIVE experimentation must
# supply them explicitly in its own environment, in addition to bypassing no supported CLI.
USDC_ADDRESS = os.getenv("POLYGON_USDC_ADDRESS", "").strip()
CTF_EXCHANGE = os.getenv("POLYMARKET_CTF_EXCHANGE_ADDRESS", "").strip()
NEG_RISK_EXCHANGE = os.getenv("POLYMARKET_NEG_RISK_EXCHANGE_ADDRESS", "").strip()
ROUTER = os.getenv("POLYMARKET_ROUTER_ADDRESS", "").strip()
CONDITIONAL_TOKENS = os.getenv("POLYMARKET_CONDITIONAL_TOKENS_ADDRESS", "").strip()

# --- Gas ---
MAX_FEE_PER_GAS = 200e9  # 200 gwei

# =============================================================================
# MATH
# =============================================================================


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bucket_prob(forecast, bucket: TemperatureBucket, sigma=2.0):
    """Probability for the shared whole-degree temperature bucket contract."""
    return bucket.probability(forecast, sigma)


def calc_ev(p, price):
    if price <= 0 or price >= 1:
        return 0.0
    win = p * (1.0 / price - 1.0)
    lose = (1.0 - p) * 1.0
    return win - lose


def calc_kelly(p, price):
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    if b <= 0:
        return 0.0
    raw = (p * b - (1.0 - p)) / b
    return round(min(max(raw, 0.0) * KELLY_FRAC, 1.0), 4)


def bet_size(kelly):
    return round(min(BALANCE * kelly, MAX_BET), 2)


# =============================================================================
# ADAPTIVE LEARNING
# =============================================================================

LEARNING_DIR = BOT_DIR / "data" / "learning"
LEARNING_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = LEARNING_DIR / "model.json"


def _default_model():
    return {
        "version": 1,
        "global": {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
        "city_knowledge": {},
        "bucket_knowledge": {},
        "kelly_adjustment": 1.0,
        "max_kelly_frac": KELLY_FRAC,
        "ev_floor": MIN_EV,
        "confidence": 0.0,
    }


def _load_model():
    try:
        return json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_model()


def _save_model(model):
    MODEL_PATH.write_text(json.dumps(model, indent=2, sort_keys=True), encoding="utf-8")


def record_trade(trade):
    model = _load_model()
    resolved = [item for item in load_all_markets() if item.get("status") == "resolved"]
    total_trades = len(resolved)
    total_wins = sum(1 for item in resolved if item.get("outcome") == "win")
    total_losses = sum(1 for item in resolved if item.get("outcome") == "loss")
    total_pnl = sum(item.get("pnl", 0) for item in resolved)
    winrate = total_wins / total_trades if total_trades else 0.0
    model["global"] = {
        "trades": total_trades,
        "wins": total_wins,
        "losses": total_losses,
        "total_pnl": round(total_pnl, 4),
    }

    # City-level knowledge
    for city in set(item["city"] for item in resolved):
        city_trades = [item for item in resolved if item["city"] == city]
        city_wins = sum(1 for item in city_trades if item["outcome"] == "win")
        city_losses = sum(1 for item in city_trades if item["outcome"] == "loss")
        city_pnl = sum(item["pnl"] for item in city_trades)
        model["city_knowledge"][city] = {
            "wins": city_wins,
            "losses": city_losses,
            "total_pnl": round(city_pnl, 4),
            "trades": len(city_trades),
        }

    # Bucket-level knowledge
    for bucket in set(item["bucket"] for item in resolved):
        b_trades = [item for item in resolved if item["bucket"] == bucket]
        b_wins = sum(1 for item in b_trades if item["outcome"] == "win")
        b_losses = sum(1 for item in b_trades if item["outcome"] == "loss")
        model["bucket_knowledge"][bucket] = {
            "wins": b_wins,
            "losses": b_losses,
        }

    # Adaptive Kelly: lower if winrate < 50% or poor PnL
    if total_trades >= 5:
        if winrate < 0.45 or total_pnl < -1.0:
            model["kelly_adjustment"] = max(0.25, model["kelly_adjustment"] * 0.8)
            model["ev_floor"] = min(0.20, model["ev_floor"] * 1.1)
        elif winrate > 0.55 and total_pnl > 2.0:
            model["kelly_adjustment"] = min(1.0, model["kelly_adjustment"] * 1.1)
            model["ev_floor"] = max(MIN_EV, model["ev_floor"] * 0.95)

        model["max_kelly_frac"] = round(KELLY_FRAC * model["kelly_adjustment"], 4)
        model["confidence"] = min(1.0, total_trades / 20.0)

    _save_model(model)


def get_adjusted_kelly(base_kelly: float) -> float:
    """Apply learned adjustment to Kelly fraction."""
    model = _load_model()
    adj = model.get("kelly_adjustment", 1.0)
    capped = min(base_kelly * adj, model.get("max_kelly_frac", KELLY_FRAC))
    return round(capped, 4)


def get_adjusted_ev_floor() -> float:
    """Get adaptive EV threshold based on recent performance."""
    model = _load_model()
    return model.get("ev_floor", MIN_EV)


def get_city_winrate(city_slug: str) -> float:
    """Get learned winrate for a specific city (0.5 if unknown)."""
    model = _load_model()
    city = model.get("city_knowledge", {}).get(city_slug)
    if not city or city["trades"] < 2:
        return 0.5
    total = city["wins"] + city["losses"]
    return city["wins"] / total


def get_learning_stats() -> dict:
    """Return current learning model summary."""
    model = _load_model()
    g = model.get("global", {})
    trades = g.get("trades", 0)
    if trades == 0:
        return {
            "trades": 0,
            "winrate": "N/A",
            "pnl": "$0.00",
            "confidence": "0%",
            "kelly_adj": "1.0x",
            "ev_floor": f"{MIN_EV * 100:.0f}%",
        }
    wr = g.get("wins", 0) / trades
    return {
        "trades": trades,
        "winrate": f"{wr:.0%}",
        "pnl": f"${g.get('total_pnl', 0):.2f}",
        "confidence": f"{model.get('confidence', 0) * 100:.0f}%",
        "kelly_adj": f"{model.get('kelly_adjustment', 1.0):.2f}x",
        "ev_floor": f"{model.get('ev_floor', MIN_EV) * 100:.0f}%",
    }


# =============================================================================
# CLOB CLIENT
# =============================================================================

from weatherbot.polymarket.legacy import (
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    UnsupportedTradingClient,
)

_clob: UnsupportedTradingClient | None = None


def get_clob() -> UnsupportedTradingClient:
    global _clob
    if _clob is None:
        _clob = UnsupportedTradingClient(
            signature_type=SIG_TYPE,
            wallet_address=WALLET or None,
        )
    return _clob


# =============================================================================
# ON-CHAIN HELPERS
# =============================================================================

_w3: Any = None


def _web3_class():
    require_live_dependencies()
    from web3 import Web3

    return Web3


def get_w3():
    global _w3
    if _w3 is None:
        web3_class = _web3_class()
        _w3 = web3_class(web3_class.HTTPProvider("https://1rpc.io/matic"))
    return _w3


def get_nonce(wallet: str) -> int:
    return get_w3().eth.get_transaction_count(wallet)


def send_tx(w3, signed_txn):
    return w3.eth.send_raw_transaction(signed_txn).hex()


def wait_for_receipt(w3, tx_hash: str, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt and receipt["status"] == 1:
                return receipt
        except Exception:
            pass
        time.sleep(2)
    return None


# =============================================================================
# BALANCE CHECK
# =============================================================================
def get_usdc_balance(wallet: str) -> float:
    """Get USDC.e balance on Polygon via raw eth_call (avoids web3 contract ABI issues)."""
    w3 = get_w3()
    web3_class = _web3_class()
    wallet_checksum = web3_class.to_checksum_address(wallet)
    usdc_checksum = web3_class.to_checksum_address(USDC_ADDRESS)

    # balanceOf(address) — the "data" is the function selector hash + padded address
    selector = "0x70a08231"  # balanceOf(address)
    data = selector + wallet_checksum[2:].lower().rjust(64, "0")

    try:
        result = w3.eth.call(
            {
                "to": usdc_checksum,
                "data": data,
            }
        )
        bal = int.from_bytes(result, "big")
        return bal / 1e6  # USDC.e = 6 decimals
    except Exception as e:
        warn(f"Balance check failed: {e}")
        return 0.0


def get_pol_balance(wallet: str) -> float:
    w3 = get_w3()
    bal = w3.eth.get_balance(_web3_class().to_checksum_address(wallet))
    return int(bal) / 1e18


# =============================================================================
# TELEGRAM NOTIFICATIONS
# =============================================================================

_tg_session = requests.Session()


def send_telegram(text: str, retry=2) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(retry + 1):
        try:
            r = _tg_session.post(url, json=payload, timeout=(5, 10))
            if r.status_code == 200:
                return True
        except Exception:
            pass
        if attempt < retry:
            time.sleep(1)
    return False


def tg_signal(
    city: str,
    horizon: str,
    date: str,
    bucket_label: str,
    forecast_temp: float,
    entry_price: float,
    cost: float,
    ev: float,
    kelly: float,
    success: bool,
    mode: ExecutionMode,
    reason: str = "",
    status_label: str | None = None,
):
    """Send a trade signal notification to Telegram."""
    mode_header = f"🔒 <b>{mode.value.upper()} MODE</b>\n"
    if success:
        msg = (
            mode_header + f"📍 <b>{city} {horizon}</b> — {date}\n"
            f"🌡 Forecast: <b>{forecast_temp}°F</b>\n"
            f"🎯 Bucket: <b>{bucket_label}</b>\n"
            f"💰 Cost: <b>${cost:.2f}</b> @ <b>${entry_price:.3f}</b>\n"
            f"📈 EV: <b>+{ev:.2f}</b> | Kelly: <b>{kelly:.2f}</b>\n"
            f"✅ <b>{status_label or 'ORDER FILLED'}</b>"
        )
    else:
        msg = (
            mode_header + f"📍 <b>{city} {horizon}</b> — {date}\n"
            f"🌡 Forecast: <b>{forecast_temp}°F</b>\n"
            f"🎯 Bucket: <b>{bucket_label}</b>\n"
            f"❌ <b>ORDER FAILED:</b> {reason}"
        )
    send_telegram(msg)


def tg_scan_summary(
    new_trades: int,
    errors: int,
    balance: float | None,
    cities: int,
    mode: ExecutionMode,
    observed_signals: int = 0,
    paper_candidates: int = 0,
    top_signals: list = None,
    open_positions: list = None,
):
    """Send a mode-labelled scan summary to Telegram."""
    status_emoji = "✅" if errors == 0 else "⚠️"
    lines = [
        f"🔒 <b>{mode.value.upper()} MODE</b>",
        "🔔 <b>Weather Bot — Scan Report</b>",
        f"{status_emoji} Cities: {cities} | New trades: {new_trades} | Errors: {errors}",
    ]
    if mode is ExecutionMode.RESEARCH:
        lines.append(f"🔎 Signals observed: {observed_signals}")
    elif mode is ExecutionMode.PAPER:
        lines.append(f"📝 Paper candidates: {paper_candidates}")
    if balance is None:
        lines.append("💰 Wallet access: <b>disabled</b>")
    else:
        lines.append(f"💰 Balance: <b>${balance:.4f}</b> USDC.e")

    if open_positions:
        lines.append("")
        lines.append(f"📊 <b>Open Positions ({len(open_positions)}):</b>")
        for pos in open_positions[:5]:
            label = f"{pos['bucket_low']}-{pos['bucket_high']}°F"
            pnl_str = f"${pos.get('pnl', 0):.2f}" if pos.get("pnl") else "pending"
            entry = pos.get("entry_price", 0)
            cost = pos.get("cost", 0)
            lines.append(
                f"  • {pos['city_name']} {pos['date']} | {label} | "
                f"entry ${entry:.3f} | cost ${cost:.2f} | PnL {pnl_str}"
            )
        if len(open_positions) > 5:
            lines.append(f"  ...and {len(open_positions) - 5} more")
    else:
        lines.extend(("", "📊 <b>Open Positions:</b> 0"))

    if top_signals:
        lines.append("")
        lines.append(f"🎯 <b>Top EV Signals ({len(top_signals)} found):</b>")
        for sig in top_signals[:5]:
            lines.append(
                f"  • {sig['city']} {sig['horizon']} | "
                f"{sig['bucket']} | EV <b>+{sig['ev']:.2f}</b> | "
                f"${sig['price']:.3f} (market) vs ${sig['true_prob']:.3f} (model)"
            )

    send_telegram("\n".join(lines))


# =============================================================================
# APPROVAL CHECK
# =============================================================================


def is_approved(token: str, spender: str, wallet: str) -> bool:
    """Check if spender is approved for token (USDC.e)."""
    w3 = get_w3()
    usdc_abi = [
        {
            "name": "allowance",
            "inputs": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
            ],
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        }
    ]
    web3_class = _web3_class()
    usdc = w3.eth.contract(address=web3_class.to_checksum_address(token), abi=usdc_abi)
    try:
        allowance = usdc.functions.allowance(
            web3_class.to_checksum_address(wallet),
            web3_class.to_checksum_address(spender),
        ).call()
        return allowance > 0
    except Exception:
        return False


def approve_token(
    token: str,
    spender: str,
    wallet: str,
    private_key: str,
    amount_wei: int = 2**256 - 1,
    max_fee: int = MAX_FEE_PER_GAS,
):
    """Approve spender to spend token on behalf of wallet."""
    w3 = get_w3()
    usdc_abi = [
        {
            "name": "approve",
            "inputs": [
                {"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "outputs": [{"name": "", "type": "bool"}],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]
    web3_class = _web3_class()
    usdc = w3.eth.contract(address=web3_class.to_checksum_address(token), abi=usdc_abi)
    nonce = get_nonce(wallet)
    build = usdc.functions.approve(
        web3_class.to_checksum_address(spender), amount_wei
    ).build_transaction(
        {
            "from": wallet,
            "nonce": nonce,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": 25e9,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(build, private_key)
    tx_hash = send_tx(w3, signed.raw_transaction)
    live(f"Approve tx: {tx_hash}")
    receipt = wait_for_receipt(w3, tx_hash)
    if receipt:
        ok(f"Approved {spender} for {token[:10]}...")
        return True
    warn(f"Approval tx failed: {tx_hash}")
    return False


def ensure_approvals():
    """Ensure all required approvals are set before trading."""
    wallet = WALLET
    required = [
        (USDC_ADDRESS, CTF_EXCHANGE),
        (USDC_ADDRESS, NEG_RISK_EXCHANGE),
        (USDC_ADDRESS, ROUTER),
    ]
    for token, spender in required:
        if not is_approved(token, spender, wallet):
            warn(f"Missing approval: {spender[:10]} for {token[:10]}")
            ok(f"Approving {spender[:10]}...")
            approve_token(token, spender, wallet, PK)
            time.sleep(5)  # Wait for confirmation
        else:
            ok(f"Already approved: {spender[:10]}")


# =============================================================================
# ORDER EXECUTION
# =============================================================================


def place_buy_order(
    market_id: str,
    validated_quote: ValidatedExecutableQuote,
    private_key: str,
    wallet: str,
) -> dict:
    """Submit exactly the already-validated token, notional, shares, and price limit."""
    quote = validated_quote.quote
    token_id = str(quote.token_id)
    amount = float(quote.total_cost)
    price_limit = float(quote.worst_price)
    shares = float(quote.shares)

    if not is_approved(USDC_ADDRESS, ROUTER, wallet):
        return {"success": False, "reason": "Router approval missing"}

    # For BUY, amount is the validated displayed-book notional. The worst executable
    # price is a limit, not a multiplier used to reconstruct or enlarge that amount.
    order_args = MarketOrderArgs(
        token_id=token_id,
        amount=amount,
        side="BUY",
        price=price_limit,
    )

    try:
        clob = get_clob()
        # assert_level_1_auth first (fast, with timeout)
        auth_ok = _timeout_call(clob.assert_level_1_auth, timeout=10.0)
        if auth_ok is None:
            return {"success": False, "reason": "CLOB auth timeout (>10s)"}

        # create_market_order (network call, with 10s timeout)
        order_result = _timeout_call(clob.create_market_order, args=(order_args,), timeout=10.0)
        if order_result is None:
            return {"success": False, "reason": "Order execution timeout (>10s)"}

        live(f"Market order placed: {order_result}")

    except Exception as e:
        return {"success": False, "reason": f"Order failed: {e}"}

    return {
        "success": True,
        "market_id": market_id,
        "token_id": token_id,
        "price": price_limit,
        "shares": shares,
        "cost": amount,
        "all_in_cost": float(validated_quote.total_all_in_cost),
        "quote_fingerprint": validated_quote.fingerprint,
        "order_id": order_result.get("orderID")
        if isinstance(order_result, dict)
        else str(order_result),
    }


def cancel_order(order_id: str) -> bool:
    """Cancel a specific order by ID."""
    clob = get_clob()
    try:
        clob.cancel(order_id)
        ok(f"Cancelled order: {order_id[:20]}...")
        return True
    except Exception as e:
        warn(f"Cancel failed: {e}")
        return False


def cancel_all_orders() -> int:
    """Cancel all open orders. Returns count of cancelled orders."""
    clob = get_clob()
    try:
        result = clob.cancel_all()
        count = result.get("count", 0) if isinstance(result, dict) else 0
        ok(f"Cancelled {count} orders")
        return count
    except Exception as e:
        warn(f"Cancel all failed: {e}")
        return 0


# =============================================================================
# LOCATIONS & WEATHER DATA
# =============================================================================

LOCATIONS = {
    "nyc": {
        "lat": 40.7772,
        "lon": -73.8726,
        "name": "New York City",
        "station": "KLGA",
        "unit": "F",
        "region": "us",
    },
    "chicago": {
        "lat": 41.9742,
        "lon": -87.9073,
        "name": "Chicago",
        "station": "KORD",
        "unit": "F",
        "region": "us",
    },
    "miami": {
        "lat": 25.7959,
        "lon": -80.2870,
        "name": "Miami",
        "station": "KMIA",
        "unit": "F",
        "region": "us",
    },
    "dallas": {
        "lat": 32.8471,
        "lon": -96.8518,
        "name": "Dallas",
        "station": "KDAL",
        "unit": "F",
        "region": "us",
    },
    "seattle": {
        "lat": 47.4502,
        "lon": -122.3088,
        "name": "Seattle",
        "station": "KSEA",
        "unit": "F",
        "region": "us",
    },
    "atlanta": {
        "lat": 33.6407,
        "lon": -84.4277,
        "name": "Atlanta",
        "station": "KATL",
        "unit": "F",
        "region": "us",
    },
}

TIMEZONES = {
    "nyc": "America/New_York",
    "chicago": "America/Chicago",
    "miami": "America/New_York",
    "dallas": "America/Chicago",
    "seattle": "America/Los_Angeles",
    "atlanta": "America/New_York",
}

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def get_ecmwf(city_slug, dates):
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
            return {
                market_date.isoformat(): forecast for market_date, forecast in forecasts.items()
            }
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


# =============================================================================
# POLYMARKET
# =============================================================================


def get_polymarket_event(city_slug, month, day, year):
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=(5, 8))
        data = r.json()
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
    except Exception as e:
        warn(f"Polymarket API error: {e}")
    return None


def get_market_price(market_id):
    try:
        r = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}", timeout=(3, 5))
        data = r.json()
        prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
        return float(prices[0]), float(prices[1]) if len(prices) > 1 else float(prices[0])
    except Exception:
        return None, None


def parse_temp_range(question):
    """Compatibility wrapper around the shared typed temperature parser."""
    try:
        return parse_temperature_bucket(question)
    except TemperatureMarketError:
        return None


def hours_to_resolution(end_date_str):
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0


def in_bucket(forecast, bucket: TemperatureBucket):
    return bucket.contains_forecast(forecast)


# =============================================================================
# STATE (local JSON)
# =============================================================================

DATA_DIR = BOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
MARKETS_DIR = DATA_DIR / "markets"
MARKETS_DIR.mkdir(exist_ok=True)
STATE_FILE = DATA_DIR / "state_v3.json"


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "balance": 0.0,
        "starting_balance": 0.0,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "open_orders": {},
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def market_path(city_slug, date_str):
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"


def load_market(city_slug, date_str):
    p = market_path(city_slug, date_str)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def save_market(market):
    p = market_path(market["city"], market["date"])
    p.write_text(json.dumps(market, indent=2, ensure_ascii=False), encoding="utf-8")


def load_all_markets():
    markets = []
    for f in MARKETS_DIR.glob("*.json"):
        try:
            markets.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return markets


# =============================================================================
# SIGMA (weather forecast uncertainty)
# =============================================================================

SIGMA_F = 2.0


def get_sigma(city_slug):
    return SIGMA_F  # Flat sigma for now; calibration can be added later


# =============================================================================
# OPEN POSITIONS from CLOB
# =============================================================================


def get_clob_positions():
    """Get all open orders/positions from CLOB."""
    clob = get_clob()
    try:
        orders = clob.get_orders()
        return orders if orders else []
    except Exception as e:
        warn(f"Failed to fetch CLOB orders: {e}")
        return []


# =============================================================================
# SCAN & TRADE (one shot)
# =============================================================================


def _fetch_token_order_book(
    condition_id: ConditionId, token_id: OutcomeTokenId
):
    """Fetch one public CLOB token book; no wallet or authenticated write client is used."""
    response = requests.get(
        f"{CLOB_HOST}/book",
        params={"token_id": str(token_id)},
        timeout=(3, 6),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OrderBookError("CLOB order-book response must be an object")
    return parse_order_book(
        payload,
        expected_condition_id=condition_id,
        expected_token_id=token_id,
    )


def _fetch_selected_order_book(selection):
    """Fetch the selected token book; point-in-time freshness is checked centrally."""
    return _fetch_token_order_book(selection.condition_id, selection.token_id)


def _parse_api_datetime(value, *, label):
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
    try:
        depth_policy = DepthPolicy(QUOTE_DEPTH_POLICY)
    except ValueError as exc:
        raise ValueError(f"invalid quote_depth_policy: {QUOTE_DEPTH_POLICY!r}") from exc
    return CostPolicy(
        platform_fee_rate=PLATFORM_FEE_RESERVE_RATE,
        transaction_cost=TRANSACTION_COST_RESERVE,
        safety_margin_rate=MARKET_SAFETY_MARGIN_RATE,
        maximum_average_slippage=MAX_AVERAGE_SLIPPAGE,
        maximum_worst_slippage=MAX_WORST_SLIPPAGE,
        maximum_all_in_price=MAX_ALL_IN_PRICE,
        minimum_expected_return=get_adjusted_ev_floor(),
        depth_policy=depth_policy,
    )


def _parse_temperature_markets(event):
    parsed = []
    raw_markets = event.get("markets", [])
    if not isinstance(raw_markets, list):
        raise GammaMarketError("event.markets must be an array")
    for raw_market in raw_markets:
        if not isinstance(raw_market, dict):
            raise GammaMarketError("event market entry must be an object")
        market = parse_gamma_binary_market(raw_market)
        bucket = parse_temperature_bucket(market.question)
        try:
            volume = float(raw_market.get("volume", 0))
        except (TypeError, ValueError) as exc:
            raise GammaMarketError(
                f"market {market.identity.market_id} has invalid volume"
            ) from exc
        parsed.append({"market": market, "bucket": bucket, "volume": volume})
    partition = TemperatureMarketPartition(tuple(item["bucket"] for item in parsed))
    return parsed, partition


def _timeout_call(func, args=(), kwargs=None, timeout=10.0):
    """Run blocking call with timeout in daemon thread. Returns None on timeout."""
    result = [None]
    exc_holder = [None]

    def target():
        try:
            result[0] = func(*args, **(kwargs or {}))
        except BaseException as exc:
            exc_holder[0] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    if exc_holder[0] is not None:
        raise exc_holder[0]
    return result[0]


def scan_and_trade(context: ExecutionContext):
    """Historical pre-boundary scanner. Supported public and PAPER CLIs do not call it."""
    if context.mode is not ExecutionMode.RESEARCH:
        raise ModeConfigurationError("historical scanner is quarantined to RESEARCH")

    # Legacy strategy body retained below only for archaeology. It is no longer the
    # supported product runtime and should not be extended.
    now = datetime.now(timezone.utc)
    errors = []
    new_trades = 0
    observed_signals = 0
    paper_candidates = 0
    top_signals = []

    for city_slug, loc in LOCATIONS.items():
        calendar = MarketCalendar(TIMEZONES[city_slug])
        local_today = calendar.local_date(now)
        dates = [(local_today + timedelta(days=offset)).isoformat() for offset in range(4)]
        forecasts = get_forecast_snapshot(city_slug, dates)
        for market_date, weather in forecasts.items():
            event_date = datetime.strptime(market_date, "%Y-%m-%d").date()
            month = MONTHS[event_date.month - 1]
            event = get_polymarket_event(city_slug, month, event_date.day, event_date.year)
            if not event:
                continue
            try:
                selected, partition = _parse_temperature_markets(event)
            except (GammaMarketError, TemperatureMarketError) as exc:
                errors.append(str(exc))
                continue
            target_bucket = partition.bucket_for_forecast(weather.signal_temperature_f)
            for item in selected:
                if item["bucket"] != target_bucket:
                    continue
                market = item["market"]
                selection = market.select(BinaryOutcome.YES)
                try:
                    book = _fetch_selected_order_book(selection)
                except (requests.RequestException, OrderBookError) as exc:
                    errors.append(str(exc))
                    continue
                probability = bucket_prob(weather.signal_temperature_f, target_bucket, get_sigma(city_slug))
                kelly = get_adjusted_kelly(calc_kelly(probability, float(book.best_ask)))
                budget = bet_size(kelly)
                if budget <= 0:
                    continue
                event_snapshot = MarketEventSnapshot(
                    event_id=str(event.get("id", "")),
                    retrieved_at_utc=now,
                    source_updated_at_utc=_parse_api_datetime(
                        event.get("updatedAt"), label="event.updatedAt"
                    ),
                )
                try:
                    evaluation = evaluate_executable_buy(
                        weather=weather,
                        event=event_snapshot,
                        order_book=book,
                        balance=None,
                        requested_budget=budget,
                        probability=probability,
                        evaluated_at=now,
                        freshness_policy=_quote_freshness_policy(),
                        cost_policy=_quote_cost_policy(),
                    )
                except Exception as exc:
                    errors.append(str(exc))
                    continue
                if evaluation.accepted:
                    observed_signals += 1
                    top_signals.append(
                        {
                            "city": loc["name"],
                            "horizon": market_date,
                            "bucket": target_bucket.label,
                            "ev": float(evaluation.quote.expected_return) if evaluation.quote else 0.0,
                            "price": float(book.best_ask),
                            "true_prob": probability,
                        }
                    )

    return {
        "new_trades": new_trades,
        "errors": errors,
        "observed_signals": observed_signals,
        "paper_candidates": paper_candidates,
        "top_signals": top_signals,
    }


def run_resolution_monitor_cycle(ledger_path: Path):
    return resolve_ledger_positions(ledger_path)


def run_loop(context: ExecutionContext):
    while True:
        scan_and_trade(context)
        time.sleep(SCAN_INTERVAL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quarantined legacy WeatherBot v3 implementation")
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=("scan", "run", "status", "resolve", "cancel", "paper-reset"),
    )
    parser.add_argument("--mode", choices=("research", "paper", "live"), default="research")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-reset", action="store_true")
    return parser


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    context = resolve_execution_context(
        configured_mode=str(_cfg.get("mode", "research")),
        requested_mode=args.mode,
        confirm_live=args.confirm_live,
    )
    if context.mode is ExecutionMode.LIVE:
        raise ModeConfigurationError("legacy LIVE operation is quarantined")
    if args.command == "scan":
        return scan_and_trade(context)
    if args.command == "run":
        return run_loop(context)
    if args.command == "resolve":
        return run_resolution_monitor_cycle(PAPER_RUNTIME.ledger_path)
    if args.command == "status":
        return load_state()
    if args.command == "paper-reset":
        raise ModeConfigurationError("use python -m weatherbot.paper reset")
    if args.command == "cancel":
        raise ModeConfigurationError("legacy cancellation is quarantined")
    raise ModeConfigurationError(f"unsupported legacy command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except ModeConfigurationError as exc:
        err(str(exc))
        raise SystemExit(2) from exc
