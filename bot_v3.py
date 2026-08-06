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
PK        = os.getenv("PK", "")
WALLET    = os.getenv("WALLET", "")
SIG_TYPE  = int(os.getenv("SIG_TYPE", "0"))

# --- Trading ---
MAX_BET       = _cfg.get("max_bet", 2.0)
MIN_EV        = _cfg.get("min_ev", 0.10)
MAX_PRICE     = _cfg.get("max_price", 0.45)
MIN_VOLUME    = _cfg.get("min_volume", 500)
MIN_HOURS     = _cfg.get("min_hours", 2.0)
MAX_HOURS     = _cfg.get("max_hours", 72.0)
KELLY_FRAC    = _cfg.get("kelly_fraction", 0.25)
MAX_SLIPPAGE  = _cfg.get("max_slippage", 0.03)
SCAN_INTERVAL = _cfg.get("scan_interval", 3600)

# --- CLOB ---
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID  = 137   # Polygon

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- Contract addresses (Polygon) ---
USDC_ADDRESS            = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE            = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE       = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
ROUTER                  = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CONDITIONAL_TOKENS      = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# --- Gas ---
MAX_FEE_PER_GAS = 200e9   # 200 gwei

# =============================================================================
# MATH
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast, t_low, t_high, sigma=2.0):
    """
    Gaussian probability that forecast falls in [t_low, t_high].
    Uses error function (math.erf) — no scipy needed.
    """
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / sigma)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / sigma)
    # Bounded range: P(t_low <= X <= t_high) = CDF(t_high) - CDF(t_low)
    z_low  = (t_low  - float(forecast)) / sigma
    z_high = (t_high - float(forecast)) / sigma
    return norm_cdf(z_high) - norm_cdf(z_low)

def calc_ev(p, price):
    if price <= 0 or price >= 1: return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price):
    if price <= 0 or price >= 1: return 0.0
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
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    RESET  = "\033[0m"
    BOLD   = "\033[1m"

def ok(msg):   print(f"{C.GREEN}  ✅ {msg}{C.RESET}")
def warn(msg): print(f"{C.YELLOW}  ⚠️  {msg}{C.RESET}")
def info(msg): print(f"{C.CYAN}  {msg}{C.RESET}")
def skip(msg): print(f"{C.GRAY}  ⏸️  {msg}{C.RESET}")
def live(msg): print(f"{C.GREEN}  {msg}{C.RESET}")

# =============================================================================
# TIMEOUT WRAPPER — prevents CLOB/HTTP calls from hanging forever
# =============================================================================

def _timeout_call(func: Callable, args: tuple = (), kwargs: dict = None,
                  timeout: float = 10.0, default: Any = None) -> Any:
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
    "city_knowledge": {},      # city_slug -> {wins, losses, total_pnl, trades}
    "bucket_knowledge": {},   # bucket_range -> {wins, losses}
    "global": {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0},
    "kelly_adjustment": 1.0,  # multiplier on Kelly fraction
    "ev_floor": MIN_EV,       # adaptive EV threshold
    "max_kelly_frac": KELLY_FRAC,
    "confidence": 0.0,        # 0-1, how much to trust learned params
}

def _load_model() -> dict:
    if MODEL_FILE.exists():
        return json.loads(MODEL_FILE.read_text(encoding="utf-8"))
    return _DEFAULT_MODEL.copy()

def _save_model(model: dict):
    MODEL_FILE.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")

def record_trade(city_slug: str, bucket_low: int, bucket_high: int,
                outcome: str, pnl: float, cost: float, kelly: float, ev: float):
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
        "bucket": f"{bucket_low}-{bucket_high}",
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
    avg_loss = abs(sum(t["pnl"] for t in resolved if t["outcome"] == "loss") / losses) if losses > 0 else 1.0

    # Global update
    model["global"] = {
        "wins": wins, "losses": losses,
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
            "wins": city_wins, "losses": city_losses,
            "total_pnl": round(city_pnl, 4),
            "trades": len(city_trades),
        }

    # Bucket-level knowledge
    for bucket in set(t["bucket"] for t in resolved):
        b_trades = [t for t in resolved if t["bucket"] == bucket]
        b_wins = sum(1 for t in b_trades if t["outcome"] == "win")
        b_losses = sum(1 for t in b_trades if t["outcome"] == "loss")
        model["bucket_knowledge"][bucket] = {
            "wins": b_wins, "losses": b_losses,
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
        return {"trades": 0, "winrate": "N/A", "pnl": "$0.00", "confidence": "0%",
                "kelly_adj": "1.0x", "ev_floor": f"{MIN_EV*100:.0f}%"}
    wr = g.get("wins", 0) / trades
    return {
        "trades": trades,
        "winrate": f"{wr:.0%}",
        "pnl": f"${g.get('total_pnl', 0):.2f}",
        "confidence": f"{model.get('confidence', 0)*100:.0f}%",
        "kelly_adj": f"{model.get('kelly_adjustment', 1.0):.2f}x",
        "ev_floor": f"{model.get('ev_floor', MIN_EV)*100:.0f}%",
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

from web3 import Web3
from eth_account import Account

_w3: Web3 = None

def get_w3() -> Web3:
    global _w3
    if _w3 is None:
        _w3 = Web3(Web3.HTTPProvider("https://1rpc.io/matic"))
    return _w3

def get_nonce() -> int:
    return get_w3().eth.get_transaction_count(WALLET)

def get_gas_params() -> dict:
    """Return EIP-1559 gas params with cap."""
    w3 = get_w3()
    try:
        base_fee = w3.eth.get_block("latest")["baseFeePerGas"]
        priority = w3.to_wei(30, "gwei")
        max_fee = min(base_fee * 2 + priority, int(MAX_FEE_PER_GAS))
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority}
    except Exception:
        # Fallback legacy gas
        return {"gasPrice": min(w3.eth.gas_price, int(MAX_FEE_PER_GAS))}

def send_tx(tx: dict, label: str = "tx") -> str:
    """Sign and send transaction with timeout and receipt check."""
    w3 = get_w3()
    tx.setdefault("nonce", get_nonce())
    tx.setdefault("chainId", CHAIN_ID)
    tx.setdefault("gas", 300_000)
    tx.update(get_gas_params())

    signed = Account.sign_transaction(tx, PK)
    tx_hash = _timeout_call(w3.eth.send_raw_transaction, args=(signed.raw_transaction,), timeout=15)
    if tx_hash is None:
        raise TimeoutError(f"{label}: broadcast timed out")

    receipt = _timeout_call(w3.eth.wait_for_transaction_receipt, args=(tx_hash,),
                            kwargs={"timeout": 90}, timeout=95)
    if receipt is None:
        raise TimeoutError(f"{label}: receipt timed out — tx may still be pending: {tx_hash.hex()}")
    if receipt.status != 1:
        raise RuntimeError(f"{label}: transaction reverted: {tx_hash.hex()}")
    return tx_hash.hex()

def ensure_approvals() -> bool:
    """Set token approvals for Polymarket contracts. Safe to call multiple times."""
    info("Checking token approvals...")
    w3 = get_w3()

    # ERC20 minimal ABI
    erc20_abi = [
        {"constant": True, "inputs": [{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
         "name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
        {"constant": False, "inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
         "name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    ]
    erc1155_abi = [
        {"constant": True, "inputs":[{"name":"account","type":"address"},{"name":"operator","type":"address"}],
         "name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"},
        {"constant": False, "inputs":[{"name":"operator","type":"address"},{"name":"approved","type":"bool"}],
         "name":"setApprovalForAll","outputs":[],"type":"function"},
    ]

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=erc20_abi)
    ctf  = w3.eth.contract(address=Web3.to_checksum_address(CONDITIONAL_TOKENS), abi=erc1155_abi)

    unlimited = 2**256 - 1
    targets = [CTF_EXCHANGE, NEG_RISK_EXCHANGE, ROUTER]
    try:
        for target in targets:
            target_cs = Web3.to_checksum_address(target)
            allowance = usdc.functions.allowance(WALLET, target_cs).call()
            if allowance < 1_000_000:
                info(f"Approving USDC → {target[:10]}...")
                tx = usdc.functions.approve(target_cs, unlimited).build_transaction({"from": WALLET})
                send_tx(tx, f"USDC approval {target[:10]}")
                ok(f"USDC approved → {target[:10]}")
            else:
                ok(f"USDC already approved → {target[:10]}")

        for target in [CTF_EXCHANGE, NEG_RISK_EXCHANGE]:
            target_cs = Web3.to_checksum_address(target)
            approved = ctf.functions.isApprovedForAll(WALLET, target_cs).call()
            if not approved:
                info(f"Approving CTF → {target[:10]}...")
                tx = ctf.functions.setApprovalForAll(target_cs, True).build_transaction({"from": WALLET})
                send_tx(tx, f"CTF approval {target[:10]}")
                ok(f"CTF approved → {target[:10]}")
            else:
                ok(f"CTF already approved → {target[:10]}")
        return True
    except Exception as e:
        warn(f"Approval failed: {e}")
        return False

# =============================================================================
# MARKET ORDER EXECUTION
# =============================================================================

def execute_trade(token_id: str, amount_usdc: float, max_price: float) -> dict:
    """
    Place FOK market buy order via CLOB.
    Returns dict: {success, order_id, price, amount, error}
    """
    result = {"success": False, "order_id": None, "price": None,
              "amount": amount_usdc, "error": None}
    try:
        client = get_clob()
        client.assert_level_1_auth()

        # Market order with max_price as worst acceptable price (slippage guard)
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount_usdc,
            side="BUY",
            price=max_price,
        )
        signed_order = _timeout_call(
            client.create_market_order,
            args=(order_args,),
            timeout=10,
        )
        if signed_order is None:
            raise TimeoutError("order creation timed out")

        response = _timeout_call(
            client.post_order,
            args=(signed_order,),
            kwargs={"order_type": OrderType.FOK},
            timeout=15,
        )
        if response is None:
            raise TimeoutError("order submission timed out — order status unknown")

        # Validate response
        if not isinstance(response, dict):
            raise RuntimeError(f"Unexpected CLOB response type: {type(response)}")

        order_id = response.get("orderID") or response.get("order_id")
        status = str(response.get("status", "")).lower()
        success = bool(response.get("success", False))

        if not order_id:
            raise RuntimeError(f"No order ID in response: {response}")
        if not success and status not in ("matched", "live", "delayed"):
            error_msg = response.get("errorMsg") or response.get("error") or f"status={status}"
            raise RuntimeError(f"Order rejected: {error_msg}")

        result.update({"success": True, "order_id": order_id,
                       "price": max_price, "status": status})
        live(f"  ✅ ORDER PLACED: {amount_usdc:.2f} USDC @ ≤{max_price:.3f} — {order_id}")
        return result

    except TimeoutError as e:
        result["error"] = str(e)
        warn(f"Trade timeout: {e}")
        return result
    except Exception as e:
        result["error"] = str(e)
        warn(f"Trade failed: {e}")
        return result

# =============================================================================
# WEATHER DATA
# =============================================================================

CITIES_F = {
    "nyc": {"lat": 40.7128, "lon": -74.0060, "tz": "America/New_York", "name": "New York"},
    "chicago": {"lat": 41.8781, "lon": -87.6298, "tz": "America/Chicago", "name": "Chicago"},
    "miami": {"lat": 25.7617, "lon": -80.1918, "tz": "America/New_York", "name": "Miami"},
    "seattle": {"lat": 47.6062, "lon": -122.3321, "tz": "America/Los_Angeles", "name": "Seattle"},
    "dallas": {"lat": 32.7767, "lon": -96.7970, "tz": "America/Chicago", "name": "Dallas"},
    "atlanta": {"lat": 33.7490, "lon": -84.3880, "tz": "America/New_York", "name": "Atlanta"},
    "denver": {"lat": 39.7392, "lon": -104.9903, "tz": "America/Denver", "name": "Denver"},
    "phoenix": {"lat": 33.4484, "lon": -112.0740, "tz": "America/Phoenix", "name": "Phoenix"},
    "la": {"lat": 34.0522, "lon": -118.2437, "tz": "America/Los_Angeles", "name": "Los Angeles"},
}

def get_forecast(city: str, target_date: str) -> dict:
    """
    Get forecast from Open-Meteo ensemble (primary) or Visual Crossing (fallback).
    Returns {temp, uncertainty, source, members} or None.
    """
    cfg = CITIES_F.get(city)
    if not cfg:
        return None

    # Primary: Open-Meteo ensemble (16 GFS members)
    try:
        url = "https://ensemble-api.open-meteo.com/v1/ensemble"
        params = {
            "latitude": cfg["lat"],
            "longitude": cfg["lon"],
            "daily": "temperature_2m_max",
            "temperature_unit": "fahrenheit",
            "timezone": cfg["tz"],
            "forecast_days": 16,
        }
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        daily = data.get("daily", {})
        times = daily.get("time", [])
        if target_date in times:
            idx = times.index(target_date)
            # Collect all ensemble member values at this date
            members = []
            for key, values in daily.items():
                if key.startswith("temperature_2m_max") and isinstance(values, list):
                    val = values[idx] if idx < len(values) else None
                    if val is not None:
                        members.append(float(val))
            if members:
                mean = sum(members) / len(members)
                variance = sum((x - mean)**2 for x in members) / len(members)
                std = math.sqrt(variance)
                return {"temp": round(mean, 2), "uncertainty": round(max(std, 1.0), 2),
                        "source": "Open-Meteo Ensemble", "members": len(members)}
    except Exception as e:
        logging.debug(f"Open-Meteo failed for {city}: {e}")

    # Fallback: Visual Crossing
    if not VC_KEY:
        logging.info("Visual Crossing fallback skipped: VC_KEY is unset")
        return None
    try:
        location = f"{cfg['lat']},{cfg['lon']}"
        url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{location}/{target_date}/{target_date}"
        params = {"unitGroup": "us", "key": VC_KEY, "include": "days"}
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        data = r.json()
        days = data.get("days", [])
        if days:
            temp = days[0].get("tempmax")
            if temp is not None:
                return {"temp": round(float(temp), 2), "uncertainty": 2.5,
                        "source": "Visual Crossing", "members": 1}
    except Exception as e:
        logging.debug(f"Visual Crossing failed for {city}: {e}")

    return None

# =============================================================================
# POLYMARKET API — CLOB-ENABLED WEATHER MARKETS ONLY
# =============================================================================

GAMMA_API = "https://gamma-api.polymarket.com"

def parse_weather_market(question: str) -> dict | None:
    """
    Parse Polymarket weather question into structured data.
    Only handles: "Will the highest temperature in [City] be between X-Y°F on [Date]?"
    Returns {city, date, t_low, t_high, unit} or None.
    """
    q = question.lower()

    # City detection
    city = None
    for slug, cfg in CITIES_F.items():
        if cfg["name"].lower() in q:
            city = slug
            break
    if not city:
        return None

    # Temperature bucket: "between 55-56°F", "55°F or below", "57°F or higher", "exactly 55°F"
    t_low = t_high = None
    m = re.search(r"between\s+(-?\d+)\s*[-–]\s*(-?\d+)\s*°?f", q)
    if m:
        t_low, t_high = int(m.group(1)), int(m.group(2))
    if t_low is None:
        m = re.search(r"(-?\d+)\s*°?f\s+or\s+below", q)
        if m: t_low, t_high = -999, int(m.group(1))
    if t_low is None:
        m = re.search(r"(-?\d+)\s*°?f\s+or\s+(?:higher|above)", q)
        if m: t_low, t_high = int(m.group(1)), 999
    if t_low is None:
        m = re.search(r"exactly\s+(-?\d+)\s*°?f", q)
        if m: t_low = t_high = int(m.group(1))
    if t_low is None:
        return None

    # Date extraction: "on March 5", "on March 5, 2025"
    months = {m.lower(): i for i, m in enumerate(
        ["January","February","March","April","May","June",
         "July","August","September","October","November","December"], 1)}
    date_match = re.search(
        r"on\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:,?\s+(\d{4}))?",
        q)
    if not date_match:
        return None
    month = months[date_match.group(1)]
    day = int(date_match.group(2))
    year = int(date_match.group(3)) if date_match.group(3) else datetime.now().year
    try:
        date_str = datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None

    return {"city": city, "date": date_str, "t_low": t_low,
            "t_high": t_high, "unit": "F"}

def fetch_weather_markets() -> list[dict]:
    """
    Fetch active weather markets from Polymarket Gamma API.
    Only returns CLOB-enabled markets with token IDs.
    """
    markets = []
    offset = 0
    limit = 100
    try:
        while True:
            r = requests.get(
                f"{GAMMA_API}/markets",
                params={"active": "true", "closed": "false",
                        "limit": limit, "offset": offset},
                timeout=15,
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for m in batch:
                question = m.get("question", "")
                if not any(word in question.lower() for word in
                           ["temperature", "°f", "degrees fahrenheit"]):
                    continue
                parsed = parse_weather_market(question)
                if not parsed:
                    continue

                # Verify CLOB-enabled and get token IDs
                clob_ids = m.get("clobTokenIds")
                if isinstance(clob_ids, str):
                    try:
                        clob_ids = json.loads(clob_ids)
                    except json.JSONDecodeError:
                        clob_ids = None
                if not clob_ids or not isinstance(clob_ids, list) or len(clob_ids) < 2:
                    continue

                outcomes = m.get("outcomes")
                if isinstance(outcomes, str):
                    try: outcomes = json.loads(outcomes)
                    except json.JSONDecodeError: outcomes = ["Yes", "No"]
                outcome_prices = m.get("outcomePrices")
                if isinstance(outcome_prices, str):
                    try: outcome_prices = json.loads(outcome_prices)
                    except json.JSONDecodeError: outcome_prices = ["0", "0"]

                # Find YES token index
                yes_idx = 0
                if outcomes:
                    for i, outcome in enumerate(outcomes):
                        if str(outcome).lower() == "yes":
                            yes_idx = i
                            break

                token_id = clob_ids[yes_idx] if yes_idx < len(clob_ids) else clob_ids[0]
                price = 0.0
                if outcome_prices and yes_idx < len(outcome_prices):
                    try: price = float(outcome_prices[yes_idx])
                    except (ValueError, TypeError): price = 0.0

                markets.append({
                    "id": m.get("id"),
                    "condition_id": m.get("conditionId"),
                    "token_id": token_id,
                    "question": question,
                    "city": parsed["city"],
                    "date": parsed["date"],
                    "t_low": parsed["t_low"],
                    "t_high": parsed["t_high"],
                    "price": price,
                    "volume": float(m.get("volumeNum") or m.get("volume") or 0),
                    "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
                    "end_date": m.get("endDate"),
                    "neg_risk": bool(m.get("negRisk", False)),
                })
            if len(batch) < limit:
                break
            offset += limit
        return markets
    except Exception as e:
        warn(f"Polymarket API error: {e}")
        return []

# =============================================================================
# TRADING SCAN
# =============================================================================

def hours_until(date_str: str) -> float:
    """Hours until end of target date (midnight UTC)."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (target - datetime.now(timezone.utc)).total_seconds() / 3600
    except (ValueError, TypeError):
        return 0.0

def scan(context: ExecutionContext):
    """Scan all CLOB-enabled weather markets and trade mispriced ones."""
    mode = context.mode
    mode_label = mode.value.upper()
    print(f"\n{C.BOLD}🌦️  Weather Market Scanner v3 [{mode_label}]{C.RESET}")
    print("═" * 50)
    if mode is ExecutionMode.LIVE:
        print(f"Wallet: {WALLET}")
    print(f"Max bet: ${MAX_BET:.2f} | Min EV: {get_adjusted_ev_floor():.0%} | Max price: {MAX_PRICE:.2f}")
    print()

    markets = fetch_weather_markets()
    info(f"Found {len(markets)} CLOB weather markets")

    candidates = []
    traded = 0

    for market in markets:
        try:
            # Time filter
            hours = hours_until(market["date"])
            if hours < MIN_HOURS or hours > MAX_HOURS:
                continue

            # Volume filter
            if market["volume"] < MIN_VOLUME:
                continue

            # Price filter
            price = market["price"]
            if price <= 0.01 or price > MAX_PRICE:
                continue

            # Forecast
            forecast = get_forecast(market["city"], market["date"])
            if not forecast:
                continue

            # Probability using ensemble uncertainty
            prob = bucket_prob(
                forecast["temp"], market["t_low"], market["t_high"],
                sigma=forecast["uncertainty"],
            )
            ev = calc_ev(prob, price)
            kelly = calc_kelly(prob, price)
            adj_kelly = get_adjusted_kelly(kelly)
            amount = bet_size(adj_kelly)

            # Adaptive EV floor
            ev_floor = get_adjusted_ev_floor()
            # City adjustment: require higher EV for historically weak cities
            city_wr = get_city_winrate(market["city"])
            if city_wr < 0.4:
                ev_floor *= 1.25

            if ev < ev_floor or amount < 0.50:
                continue

            # Candidate found
            candidate = {
                **market,
                "forecast": forecast["temp"],
                "uncertainty": forecast["uncertainty"],
                "probability": prob,
                "ev": ev,
                "kelly": adj_kelly,
                "amount": amount,
                "source": forecast["source"],
                "hours": hours,
                "city_winrate": city_wr,
            }
            candidates.append(candidate)

            print(f"\n{C.BOLD}🎯 {market['question']}{C.RESET}")
            print(f"   Forecast: {forecast['temp']:.1f}°F ±{forecast['uncertainty']:.1f}° "
                  f"({forecast['source']}, {forecast['members']} members)")
            print(f"   Our probability: {prob:.1%} | Market price: {price:.3f}")
            print(f"   EV: {ev:+.1%} | Kelly: {adj_kelly:.3f} | Bet: ${amount:.2f}")
            print(f"   Volume: ${market['volume']:,.0f} | Resolves in {hours:.1f}h")

            if mode is ExecutionMode.RESEARCH:
                skip("Research mode: signal only; no order intent is recorded")
                continue

            if mode is ExecutionMode.PAPER:
                skip("Paper candidate recorded; simulated fills and accounting belong to #27")
                continue

            # Live trading remains behind both execution-mode gates.
            # Remaining exchange and risk defects may still reject the operation.
            result = run_live_operation(
                context,
                lambda: execute_trade(
                    market["token_id"], amount, min(price + MAX_SLIPPAGE, MAX_PRICE)
                ),
            )
            if result["success"]:
                traded += 1
                record_trade(
                    market["city"], market["t_low"], market["t_high"],
                    "pending", 0.0, amount, adj_kelly, ev,
                )
                if TELEGRAM_BOT_TOKEN:
                    send_telegram(
                        f"🌦️ *Weather Trade*\n"
                        f"{market['question']}\n"
                        f"Forecast: {forecast['temp']:.1f}°F | Prob: {prob:.1%}\n"
                        f"Price: {price:.3f} | EV: {ev:+.1%}\n"
                        f"Bet: ${amount:.2f} | Order: `{result['order_id']}`"
                    )
            else:
                warn(f"  Trade failed: {result['error']}")

        except Exception as e:
            logging.exception(f"Error processing market: {e}")
            continue

    print(f"\n{'═' * 50}")
    if mode is ExecutionMode.RESEARCH:
        print(f"Research signals: {len(candidates)} | No financial state changed")
    elif mode is ExecutionMode.PAPER:
        print(f"Paper candidates: {len(candidates)} | Fills not simulated yet")
    else:
        print(f"Candidates: {len(candidates)} | Trades placed: {traded}")
    return candidates

# =============================================================================
# TELEGRAM
# =============================================================================

def send_telegram(text: str) -> bool:
    """Send Telegram notification. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("Telegram notification skipped: credentials are unset")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logging.warning("Telegram error: %s", type(e).__name__)
        return False

# =============================================================================
# POSITION MONITOR
# =============================================================================

def monitor_positions():
    """Monitor open orders and resolved positions."""
    info("Position monitor started")
    while True:
        try:
            # Get open CLOB orders
            client = get_clob()
            orders = _timeout_call(client.get_orders, timeout=10, default=[])
            if orders:
                info(f"Open orders: {len(orders)}")
            # TODO: Check resolved positions via Gamma API and update trade outcomes
        except Exception as e:
            logging.debug(f"Monitor error: {e}")
        time.sleep(60)

# =============================================================================
# CLI
# =============================================================================

def cmd_status(context: ExecutionContext):
    print(f"\n{C.BOLD}📊 Weather Bot Status{C.RESET}")
    print("═" * 45)
    print(f"Mode: {context.mode.value.upper()}")
    if context.mode is ExecutionMode.LIVE:
        print(f"Wallet: {WALLET}")
    print(credential_status_line(PK=PK, WALLET=WALLET))
    stats = get_learning_stats()
    print(f"\nLearning model:")
    print(f"  Trades: {stats['trades']} | Win rate: {stats['winrate']}")
    print(f"  Total PnL: {stats['pnl']} | Confidence: {stats['confidence']}")
    print(f"  Kelly adjustment: {stats['kelly_adj']} | EV floor: {stats['ev_floor']}")

    # CLOB status is live-only. Research and paper status are wallet-free.
    if context.mode is not ExecutionMode.LIVE:
        print("\nCLOB: not initialized outside live mode")
        return
    try:
        client = get_clob()
        client.assert_level_1_auth()
        orders = _timeout_call(client.get_orders, timeout=10, default=[])
        print(f"\nOpen CLOB orders: {len(orders) if orders else 0}")
    except Exception as e:
        print(f"\nCLOB status: unavailable ({e})")

def cmd_cancel(context: ExecutionContext, market_id: str | None = None):
    """Cancel open orders. Requires confirmed live mode before touching the client."""
    def cancel_live() -> None:
        client = get_clob()
        if market_id:
            # Fetch orders and cancel matching market
            orders = _timeout_call(client.get_orders, timeout=10, default=[])
            if not orders:
                info("No open orders")
                return
            cancelled = 0
            for order in orders:
                if order.get("market") == market_id or order.get("asset_id") == market_id:
                    oid = order.get("id") or order.get("orderID")
                    if oid:
                        _timeout_call(client.cancel, args=(oid,), timeout=10)
                        cancelled += 1
            ok(f"Cancelled {cancelled} orders for market {market_id}")
        else:
            _timeout_call(client.cancel_all, timeout=10)
            ok("All open orders cancelled")

    run_live_operation(context, cancel_live)

def run_loop(context: ExecutionContext):
    print(f"\n{C.BOLD}🚀 Weather Bot v3 starting [{context.mode.value.upper()}]{C.RESET}")
    if context.mode is ExecutionMode.LIVE:
        print(f"   Wallet: {WALLET}")
        print("   Mode: LIVE TRADING")
        if not ensure_approvals():
            warn("Some approvals may be missing — trades could fail")
    elif context.mode is ExecutionMode.PAPER:
        print("   Mode: PAPER — wallet, approvals, and live orders are disabled")
    else:
        print("   Mode: RESEARCH — wallet, approvals, and financial state are disabled")

    monitor = None
    if context.mode is ExecutionMode.LIVE:
        monitor = threading.Thread(target=monitor_positions, daemon=True)
        monitor.start()

    try:
        while True:
            scan(context)
            info(f"Next scan in {SCAN_INTERVAL // 60} minutes")
            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print("\nBot stopped")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Weather-market research, paper, and explicitly gated live runner",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "scan", "status", "cancel"),
        default="status",
    )
    parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in ExecutionMode),
        help="required execution mode; must agree with config.json",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="second independent gate required for live execution",
    )
    parser.add_argument("--market", help="market ID for targeted cancellation")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command == "cancel" and args.market == "all":
        args.market = None

    try:
        context = resolve_execution_context(
            config_mode=_cfg.get("mode"),
            requested_mode=args.mode,
            confirm_live=args.confirm_live,
            private_key=PK,
            wallet=WALLET,
        )
        if command == "run":
            run_loop(context)
        elif command == "scan":
            scan(context)
        elif command == "status":
            cmd_status(context)
        elif command == "cancel":
            cmd_cancel(context, args.market)
        else:
            parser.error(f"unknown command: {command}")
    except (ModeConfigurationError, LiveExecutionBlocked) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
