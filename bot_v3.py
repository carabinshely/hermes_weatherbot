#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weather Trading Bot v3 — Polymarket CLOB Real Trading
======================================================
bot_v2 strategy logic + official Polymarket public SDK boundary.
Authenticated order execution remains fail-closed.
Only trades US cities (F) for now — EU/Asia cities need CLOB market support.

Usage:
    python bot_v3.py run          # Full trading loop (scan + monitor)
    python bot_v3.py scan         # One-shot scan + trade signals
    python bot_v3.py status       # Show open positions + balance
    python bot_v3.py resolve      # Resolve and settle pending ledger positions
    python bot_v3.py cancel       # Cancel all open orders
    python bot_v3.py cancel --market <market_id>  # Cancel orders for a market
"""

import argparse
import re
import sys
import json
import math
import time
import os
import logging
import dotenv
import requests
import threading
from decimal import Decimal

from execution_modes import (
    ExecutionContext,
    ExecutionMode,
    LiveExecutionBlocked,
    ModeConfigurationError,
    require_live,
    resolve_execution_context,
    run_live_operation,
)
from runtime_security import credential_status_line
from weatherbot.dependencies import (
    LiveDependenciesUnavailable,
    require_live_dependencies,
)
from weatherbot.forecasting import (
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
    ValidatedExecutableQuote,
    evaluate_executable_buy,
    revalidate_executable_buy,
)
from weatherbot.resolution import run_resolution_cycle as resolve_ledger_positions
from weatherbot.markets import (
    BinaryOutcome,
    GammaMarketError,
    MarketCalendar,
    OrderBookError,
    TemperatureBucket,
    TemperatureMarketError,
    TemperatureMarketPartition,
    TemperatureUnit,
    parse_gamma_binary_market,
    parse_order_book,
    parse_temperature_bucket,
)
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Any

# =============================================================================
# CONFIG
# =============================================================================

BOT_DIR = Path(__file__).parent
dotenv.load_dotenv(BOT_DIR / ".env")

with open(BOT_DIR / "config.json", encoding="utf-8") as f:
    _cfg = json.load(f)

# --- Wallet ---
PK = os.getenv("PK", "")
WALLET = os.getenv("WALLET", "")
SIG_TYPE = int(os.getenv("SIG_TYPE", "0"))

# --- Trading ---
MAX_BET = _cfg.get("max_bet", 2.0)
MIN_EV = _cfg.get("min_ev", 0.10)
MAX_PRICE = _cfg.get("max_price", 0.45)
MIN_VOLUME = _cfg.get("min_volume", 500)
MIN_HOURS = _cfg.get("min_hours", 2.0)
MAX_HOURS = _cfg.get("max_hours", 72.0)
KELLY_FRAC = _cfg.get("kelly_fraction", 0.25)
MAX_SLIPPAGE = _cfg.get("max_slippage", 0.03)
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
_ledger_config_path = Path(_cfg.get("ledger_path", "state/ledger.sqlite3"))
LEDGER_PATH = (
    _ledger_config_path if _ledger_config_path.is_absolute() else BOT_DIR / _ledger_config_path
)

# --- CLOB ---
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- Contract addresses (Polygon) ---
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
ROUTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

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
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)


def calc_kelly(p, price):
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRAC, 1.0), 4)


def bet_size(kelly):
    """Calculate bet size from Kelly fraction. Always uses MAX_BET as cap for consistency."""
    raw = kelly * MAX_BET
    return round(min(raw, MAX_BET), 2)


# =============================================================================
# COLORS
# =============================================================================


class C:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def ok(msg):
    print(f"{C.GREEN}  ✅ {msg}{C.RESET}")


def warn(msg):
    print(f"{C.YELLOW}  ⚠️  {msg}{C.RESET}")


def info(msg):
    print(f"{C.CYAN}  {msg}{C.RESET}")


def skip(msg):
    print(f"{C.GRAY}  ⏸️  {msg}{C.RESET}")


def live(msg):
    print(f"{C.GREEN}  {msg}{C.RESET}")


# =============================================================================
# TIMEOUT WRAPPER — prevents CLOB/HTTP calls from hanging forever
# =============================================================================


def _timeout_call(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    timeout: float = 10.0,
    default: Any = None,
) -> Any:
    """Run func in a thread with a timeout. Returns default on timeout."""
    kwargs = kwargs or {}
    result = [default]
    error = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return default
    if error[0]:
        raise error[0]
    return result[0]


# =============================================================================
# SELF-LEARNING SYSTEM — adapts strategy based on trade history
# =============================================================================

LEARNING_DIR = BOT_DIR / "data" / "learning"
LEARNING_DIR.mkdir(exist_ok=True)
TRADE_LOG = LEARNING_DIR / "trade_log.json"
MODEL_FILE = LEARNING_DIR / "model.json"
LEARNING_WINDOW = 30  # Consider last N trades for adaptation

# Default model (conservative start)
_DEFAULT_MODEL = {
    "version": 1,
    "city_knowledge": {},  # city_slug -> {wins, losses, total_pnl, trades}
    "bucket_knowledge": {},  # bucket_range -> {wins, losses}
    "global": {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0},
    "kelly_adjustment": 1.0,  # multiplier on Kelly fraction
    "ev_floor": MIN_EV,  # adaptive EV threshold
    "max_kelly_frac": KELLY_FRAC,
    "confidence": 0.0,  # 0-1, how much to trust learned params
}


def _load_model() -> dict:
    if MODEL_FILE.exists():
        return json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    return _DEFAULT_MODEL.copy()


def _save_model(model: dict):
    MODEL_FILE.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")


def record_trade(
    city_slug: str, bucket: str, outcome: str, pnl: float, cost: float, kelly: float, ev: float
):
    """
    Record a completed trade for self-learning.
    outcome: 'win' | 'loss' | 'pending'
    pnl: profit/loss amount in USDC
    """
    model = _load_model()

    # Load existing trade log
    log = []
    if TRADE_LOG.exists():
        log = json.loads(TRADE_LOG.read_text(encoding="utf-8"))

    # Append new trade
    trade = {
        "id": len(log) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "city": city_slug,
        "bucket": bucket,
        "outcome": outcome,
        "pnl": round(pnl, 4),
        "cost": round(cost, 4),
        "kelly": round(kelly, 4),
        "ev": round(ev, 4),
    }
    log.append(trade)

    # Keep only recent trades
    log = log[-LEARNING_WINDOW:]
    TRADE_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update model based on resolved trades only
    resolved = [t for t in log if t["outcome"] in ("win", "loss")]
    if not resolved:
        _save_model(model)
        return

    wins = sum(1 for t in resolved if t["outcome"] == "win")
    losses = sum(1 for t in resolved if t["outcome"] == "loss")
    total_pnl = sum(t["pnl"] for t in resolved)
    total_trades = len(resolved)
    winrate = wins / total_trades if total_trades > 0 else 0.5

    avg_win = sum(t["pnl"] for t in resolved if t["outcome"] == "win") / wins if wins > 0 else 1.0
    avg_loss = (
        abs(sum(t["pnl"] for t in resolved if t["outcome"] == "loss") / losses)
        if losses > 0
        else 1.0
    )

    # Global update
    model["global"] = {
        "wins": wins,
        "losses": losses,
        "total_pnl": round(total_pnl, 4),
        "trades": total_trades,
    }

    # City-level knowledge
    for city in set(t["city"] for t in resolved):
        city_trades = [t for t in resolved if t["city"] == city]
        city_wins = sum(1 for t in city_trades if t["outcome"] == "win")
        city_losses = sum(1 for t in city_trades if t["outcome"] == "loss")
        city_pnl = sum(t["pnl"] for t in city_trades)
        model["city_knowledge"][city] = {
            "wins": city_wins,
            "losses": city_losses,
            "total_pnl": round(city_pnl, 4),
            "trades": len(city_trades),
        }

    # Bucket-level knowledge
    for bucket in set(t["bucket"] for t in resolved):
        b_trades = [t for t in resolved if t["bucket"] == bucket]
        b_wins = sum(1 for t in b_trades if t["outcome"] == "win")
        b_losses = sum(1 for t in b_trades if t["outcome"] == "loss")
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
            f"✅ <b>ORDER FILLED</b>"
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


def _fetch_selected_order_book(selection):
    """Fetch the selected token book; point-in-time freshness is checked centrally."""
    response = requests.get(
        f"{CLOB_HOST}/book",
        params={"token_id": str(selection.token_id)},
        timeout=(3, 6),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OrderBookError("CLOB order-book response must be an object")
    return parse_order_book(
        payload,
        expected_condition_id=selection.condition_id,
        expected_token_id=selection.token_id,
    )


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
        parsed.append(
            {
                "market": market,
                "bucket": bucket,
                "volume": volume,
            }
        )
    partition = TemperatureMarketPartition(tuple(item["bucket"] for item in parsed))
    return parsed, partition


def scan_and_trade(context: ExecutionContext):
    """Scan markets with explicit token, quote, bucket, and local-date contracts."""
    now = datetime.now(timezone.utc)
    is_live = context.mode is ExecutionMode.LIVE
    state = load_state() if is_live else {"balance": 0.0, "total_trades": 0}
    balance = None
    if is_live:
        balance = get_usdc_balance(WALLET)
        if balance != state.get("balance"):
            state["balance"] = balance
            save_state(state)

    print(f"\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — {context.label} MODE{C.RESET}")
    print("=" * 60)
    print(f"  Mode:         {context.label}")
    if is_live:
        print(f"  Wallet:       {WALLET[:8]}...{WALLET[-4:]}")
        print(f"  USDC.e:       ${balance:.4f}")
        print(f"  POL balance:  {get_pol_balance(WALLET):.4f} POL")
    else:
        print("  Wallet access: disabled")
        if context.mode is ExecutionMode.PAPER:
            print("  Paper fills:   pending implementation in #27")
    print(f"  Max bet:      ${MAX_BET} | Min EV: {MIN_EV * 100:.0f}%")
    print()

    new_trades = 0
    observed_signals = 0
    paper_candidates = 0
    errors = []
    top_signals = []

    for city_slug, loc in LOCATIONS.items():
        print(f"  -> {loc['name']}...", end=" ", flush=True)
        market_timezone = TIMEZONES[city_slug]
        calendar = MarketCalendar(market_timezone)
        dates = tuple(candidate.isoformat() for candidate in calendar.candidate_dates(now, count=4))

        try:
            t0 = time.time()
            forecasts = get_forecast_snapshot(city_slug, dates)
            info(f"[{loc['name']}] forecast loaded in {time.time() - t0:.1f}s")
            time.sleep(0.3)
        except Exception as exc:
            print(f"error ({exc})")
            continue

        city_found_signal = False
        for horizon_index, market_date in enumerate(dates):
            try:
                parsed_date = datetime.strptime(market_date, "%Y-%m-%d")
                event = get_polymarket_event(
                    city_slug,
                    MONTHS[parsed_date.month - 1],
                    parsed_date.day,
                    parsed_date.year,
                )
                event_retrieved_at = datetime.now(timezone.utc)
            except Exception as exc:
                warn(f"Polymarket error for {loc['name']} D+{horizon_index}: {exc}")
                continue
            if not event:
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
            hours = hours_to_resolution(end_date) if end_date else 0
            horizon = f"D+{horizon_index}"
            if hours < MIN_HOURS or hours > MAX_HOURS:
                continue

            weathersnap = forecasts.get(market_date)
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
            if forecast_temp < -40 or forecast_temp > 130:
                warn(f"  ⚠️  Invalid forecast temp {forecast_temp}°F — skipping city")
                break

            try:
                outcomes, partition = _parse_temperature_markets(event)
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
                book = _fetch_selected_order_book(selection)
            except (
                GammaMarketError,
                TemperatureMarketError,
                OrderBookError,
                requests.RequestException,
            ) as exc:
                errors.append(f"{loc['name']} {horizon}: {exc}")
                warn(f"  {loc['name']} {horizon} market rejected: {exc}")
                continue

            volume = selected["volume"]
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
            signal_generated_at = datetime.now(timezone.utc)
            weather_metadata = weathersnap.signal_metadata(
                generated_at_utc=signal_generated_at,
            )
            best_signal = {
                "market_id": str(selection.market_id),
                "condition_id": str(selection.condition_id),
                "outcome": selection.outcome.value,
                "token_id": str(selection.token_id),
                "question": market.question,
                "bucket_key": target_bucket.key,
                "bucket_label": target_bucket.label,
                "bucket_low": (
                    None
                    if target_bucket.lower_inclusive is None
                    else float(target_bucket.lower_inclusive)
                ),
                "bucket_high": (
                    None
                    if target_bucket.upper_inclusive is None
                    else float(target_bucket.upper_inclusive)
                ),
                "entry_price": entry_price,
                "all_in_price": all_in_price,
                "best_bid": float(quote.best_bid),
                "best_ask": float(quote.best_ask),
                "worst_price": float(quote.worst_price),
                "spread": float(book.spread),
                "execution_slippage": execution_slippage,
                "shares": shares,
                "book_cost": book_cost,
                "cost": cost,
                "platform_fee_reserve": float(validated_quote.platform_fee),
                "transaction_cost_reserve": float(validated_quote.transaction_cost),
                "safety_margin_reserve": float(validated_quote.safety_margin),
                "probability_edge": float(validated_quote.probability_edge),
                "p": round(probability, 6),
                "ev": round(ev, 4),
                "kelly": round(adjusted_kelly, 4),
                "forecast_temp": forecast_temp,
                "forecast_src": best_source,
                **weather_metadata,
                "market_date": market_date,
                "market_timezone": market_timezone,
                "signal_generated_at_utc": signal_generated_at.isoformat(),
                "order_book_observed_at_utc": quote.observed_at.isoformat(),
                "order_book_hash": quote.book_hash,
                "market_yes_price": (
                    None
                    if market.descriptive_price(BinaryOutcome.YES) is None
                    else float(market.descriptive_price(BinaryOutcome.YES))
                ),
                "market_no_price": (
                    None
                    if market.descriptive_price(BinaryOutcome.NO) is None
                    else float(market.descriptive_price(BinaryOutcome.NO))
                ),
                "sigma": sigma,
                "volume": volume,
                "event_retrieved_at_utc": event_snapshot.retrieved_at_utc.isoformat(),
                "event_source_updated_at_utc": (
                    None
                    if event_snapshot.source_updated_at_utc is None
                    else event_snapshot.source_updated_at_utc.isoformat()
                ),
                **validated_quote.metadata(),
            }

            top_signals.append(
                {
                    "city": loc["name"],
                    "horizon": horizon,
                    "bucket": target_bucket.label,
                    "ev": ev,
                    "price": all_in_price,
                    "true_prob": probability,
                }
            )
            city_found_signal = True
            info(
                f"  selected outcome={selection.outcome.value.upper()} "
                f"token={selection.token_id} condition={selection.condition_id}"
            )
            print(f"\n  {C.BOLD}📍 {loc['name']} {horizon} — {market_date}{C.RESET}")
            print(
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
            print(
                f"  {C.GREEN}  ✅ BUY SIGNAL | all-in ${cost:.2f} "
                f"(book ${book_cost:.2f}) @ ${entry_price:.3f} "
                f"[all-in ${all_in_price:.3f}] | net EV {ev:+.2f} | "
                f"Kel {adjusted_kelly:.2f}{C.RESET}"
            )

            if context.mode is ExecutionMode.RESEARCH:
                observed_signals += 1
                info("  [RESEARCH] signal observed; no order or state mutation")
                continue
            if context.mode is ExecutionMode.PAPER:
                paper_candidates += 1
                info("  [PAPER] candidate only; simulated fills are implemented in #27")
                continue

            require_live(context, operation="place order")
            assert balance is not None
            result = run_live_operation(
                context,
                operation="place order",
                callback=lambda: place_buy_order(
                    market_id=best_signal["market_id"],
                    validated_quote=validated_quote,
                    private_key=PK,
                    wallet=WALLET,
                ),
            )

            if result["success"]:
                new_trades += 1
                state["total_trades"] += 1
                balance -= best_signal["cost"]
                record_trade(
                    city_slug=city_slug,
                    bucket=best_signal["bucket_key"],
                    outcome="pending",
                    pnl=0.0,
                    cost=best_signal["cost"],
                    kelly=best_signal["kelly"],
                    ev=best_signal["ev"],
                )
                live(
                    f"  [LIVE] BUY {loc['name']} {horizon} | "
                    f"{best_signal['bucket_label']} YES @ ${best_signal['entry_price']:.3f} "
                    f"| EV {best_signal['ev']:+.2f} | ${best_signal['cost']:.2f}"
                )
                mkt_record = load_market(city_slug, market_date) or {
                    "city": city_slug,
                    "city_name": loc["name"],
                    "date": market_date,
                    "market_date": market_date,
                    "market_timezone": market_timezone,
                    "unit": "F",
                    "event_end_date": end_date,
                    "status": "open",
                    "position": None,
                }
                mkt_record["position"] = {
                    **best_signal,
                    "order_id": result.get("order_id"),
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "status": "open",
                    "closed_at": None,
                    "close_reason": None,
                    "exit_price": None,
                    "pnl": None,
                }
                save_market(mkt_record)
                tg_signal(
                    city=loc["name"],
                    horizon=horizon,
                    date=market_date,
                    bucket_label=best_signal["bucket_label"],
                    forecast_temp=best_signal["forecast_temp"],
                    entry_price=best_signal["entry_price"],
                    cost=best_signal["cost"],
                    ev=best_signal["ev"],
                    kelly=best_signal["kelly"],
                    success=True,
                    mode=context.mode,
                )
            else:
                errors.append(f"{loc['name']} {horizon}: {result['reason']}")
                warn(f"  ❌ Order failed: {result['reason']}")
                tg_signal(
                    city=loc["name"],
                    horizon=horizon,
                    date=market_date,
                    bucket_label=best_signal["bucket_label"],
                    forecast_temp=best_signal["forecast_temp"],
                    entry_price=best_signal["entry_price"],
                    cost=best_signal["cost"],
                    ev=best_signal["ev"],
                    kelly=best_signal["kelly"],
                    success=False,
                    mode=context.mode,
                    reason=result.get("reason", "unknown"),
                )

        if not city_found_signal:
            print("ok", end="", flush=True)
        print()

    top_signals.sort(key=lambda item: item["ev"], reverse=True)
    open_positions = []
    if is_live:
        markets = load_all_markets()
        open_positions = [
            market
            for market in markets
            if market.get("position") and market["position"].get("status") == "open"
        ]
        assert balance is not None
        state["balance"] = round(balance, 4)
        save_state(state)

    print(f"\n{'=' * 60}")
    print(f"  Scanned:    {len(LOCATIONS)} cities")
    print(f"  New trades: {C.GREEN}{new_trades}{C.RESET}")
    print(f"  Signals:    {observed_signals}")
    print(f"  Paper candidates: {paper_candidates}")
    print(f"  Errors:     {len(errors)}")
    if balance is None:
        print("  Wallet:     disabled")
    else:
        print(f"  Balance:    ${balance:.4f}")
    print(f"{'=' * 60}\n")

    tg_scan_summary(
        new_trades=new_trades,
        errors=len(errors),
        balance=balance,
        cities=len(LOCATIONS),
        mode=context.mode,
        observed_signals=observed_signals,
        paper_candidates=paper_candidates,
        top_signals=top_signals,
        open_positions=open_positions,
    )
    return new_trades, errors


# =============================================================================
# STATUS
# =============================================================================


def show_status(context: ExecutionContext):
    """Show status without crossing the selected mode boundary."""
    if context.mode is not ExecutionMode.LIVE:
        print(f"\n{C.BOLD}{C.CYAN}📊 Bot v3 — {context.label} MODE{C.RESET}")
        print("=" * 60)
        print("  Wallet access: disabled")
        if context.mode is ExecutionMode.PAPER:
            print("  Paper ledger:  pending implementation in #27")
        print(f"{'=' * 60}\n")
        return

    require_live(context, operation="live status")
    balance = get_usdc_balance(WALLET)
    pol_bal = get_pol_balance(WALLET)

    print(f"\n{C.BOLD}{C.CYAN}📊 Bot v3 — {context.label} MODE{C.RESET}")
    print("=" * 60)
    print(f"  Mode:      {context.label}")
    print(f"  Wallet:    {WALLET[:8]}...{WALLET[-4:]}")
    print(f"  USDC.e:    ${balance:.4f}")
    print(f"  POL:       {pol_bal:.4f}")
    print()

    # Open orders from CLOB
    orders = get_clob_positions()
    if orders:
        print(f"  Open orders: {len(orders)}")
        for o in orders:
            print(
                f"    {o.get('side', '?')} {o.get('size', '?')} @ ${o.get('price', '?')} "
                f"[{o.get('marketID', '')[:16]}...]"
            )
    else:
        print(f"  Open orders: 0")

    # Local market positions
    markets = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    if open_pos:
        print(f"\n  Open positions (local): {len(open_pos)}")
        for m in open_pos:
            pos = m["position"]
            unit_sym = "F"
            label = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"
            print(
                f"    {m['city_name']} {m['date']} | {label} | "
                f"entry ${pos['entry_price']:.3f} | cost ${pos.get('cost', 0):.2f}"
            )
    else:
        print(f"\n  Open positions: 0")

    print(f"{'=' * 60}\n")


# =============================================================================
# MAIN LOOP
# =============================================================================

MONITOR_INTERVAL = 600  # 10 minutes between monitor cycles


def run_resolution_monitor_cycle():
    """Resolve durable-ledger positions without wallet or trading-client access."""
    report = resolve_ledger_positions(LEDGER_PATH)
    if report.checked == 0:
        print(f"  Resolution: no pending ledger positions ({LEDGER_PATH})")
        return report
    print(
        f"  Resolution: checked={report.checked} resolved={report.resolved} "
        f"voided={report.voided} settled={report.settled_positions}"
    )
    for item in report.items:
        print(f"    {item.market_id}: {item.status.value} — {item.reason}")
    return report


def run_loop(context: ExecutionContext):
    print(f"\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — {context.label} MODE{C.RESET}")
    print("=" * 60)
    print(f"  Mode:     {context.label}")
    if context.mode is ExecutionMode.LIVE:
        print(f"  Wallet:   {WALLET[:8]}...{WALLET[-4:]}")
    else:
        print("  Wallet:   disabled")
    print(f"  Cities:   {len(LOCATIONS)}")
    print(f"  Max bet:  ${MAX_BET} | Kelly fraction: {KELLY_FRAC}")
    print(f"  Min EV:   {MIN_EV * 100:.0f}%")
    print(f"  Scan:     every {SCAN_INTERVAL // 60} min")
    print(f"  Monitor:  every {MONITOR_INTERVAL // 60} min")
    print()

    if context.mode is ExecutionMode.LIVE:
        require_live(context, operation="token approval")
        ok("Checking approvals...")
        ensure_approvals()
    else:
        skip("Live approvals disabled by execution mode")

    last_full_scan = 0

    while True:
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if now_ts - last_full_scan >= SCAN_INTERVAL:
            print(f"[{now_str}] Full scan...")
            try:
                new_trades, errors = scan_and_trade(context)
                last_full_scan = time.time()
            except Exception as e:
                warn(f"Scan error: {e}")
                time.sleep(60)
                continue
        else:
            print(f"[{now_str}] Monitoring...")
            try:
                run_resolution_monitor_cycle()
            except Exception as exc:
                warn(f"Resolution monitor error: {exc}")
            time.sleep(MONITOR_INTERVAL)


# =============================================================================
# CLI
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weather-market bot")
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=("scan", "run", "status", "resolve", "cancel"),
    )
    parser.add_argument("--mode", choices=("research", "paper", "live"))
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required in addition to config mode=live and --mode live",
    )
    parser.add_argument("--market", help="market identifier for cancellation")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        context = resolve_execution_context(
            configured_mode=_cfg.get("mode"),
            cli_mode=args.mode,
            confirm_live=args.confirm_live,
        )
    except ModeConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Execution mode: {context.label}")
    if context.mode is ExecutionMode.LIVE:
        try:
            require_live_dependencies()
        except LiveDependenciesUnavailable as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(credential_status_line())
        if not PK or not WALLET:
            print("ERROR: live mode requires PK and WALLET in .env", file=sys.stderr)
            return 2

    if args.command == "run":
        run_loop(context)
    elif args.command == "scan":
        scan_and_trade(context)
    elif args.command == "status":
        show_status(context)
    elif args.command == "resolve":
        run_resolution_monitor_cycle()
    elif args.command == "cancel":
        try:
            require_live(context, operation="order cancellation")
        except LiveExecutionBlocked as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.market:
            print(f"Cancelling orders for market: {args.market}")
        else:
            count = cancel_all_orders()
            print(f"Cancelled {count} orders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
