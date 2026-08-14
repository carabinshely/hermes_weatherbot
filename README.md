# 🌦️ WeatherBet — Powered by Hermes Agent

> **Fully Autonomous Prediction Market Trading Bot** — Uses ECMWF weather forecast data to automatically find mispriced Polymarket markets and bet on them. Self-improves over time via the **Hermes Agent** framework.

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Polygon](https://img.shields.io/badge/Chain-Polygon%20137-9B59B6.svg)](https://polygon.technology/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

> **Current #48B activation status:** RESEARCH and durable PAPER strategy scanning share the same calibrated probability boundary. PAPER always recovers its durable ledger before calibration loading, but new model-backed decisions fail closed until an accepted artifact and provable exact-run forecast evidence exist. LIVE strategy `scan/run` remains disabled. Historical target/legacy architecture sections below do not imply that automated live trading is currently enabled.

![alt text](image-1.png)
## 🤖 Why Hermes Agent

This project demonstrates the power of **Hermes Agent** framework in autonomous trading:

| Hermes Agent Feature | Application in This Project |
|---|---|
| **Self-Learning & Evolution** | Bot automatically adjusts Kelly fraction and EV threshold from trade history |
| **Fully Autonomous Execution** | 60-min scan loop → signal calculation → auto order execution → on-chain settlement — zero human intervention |
| **Multi-Platform Gateway** | Real-time trade alerts via Telegram — control everything from your phone |
| **Persistent Memory** | Trade logs + learning models persist across sessions |
| **Model Agnostic** | Switch any LLM provider for decision reasoning |
| **Tool Orchestration** | Integrates weather API + on-chain CLOB trading + Telegram notifications |

---

## 🎯 What It Does

The bot monitors **6 US cities** (NYC, Chicago, Miami, Dallas, Seattle, Atlanta) and scans Polymarket temperature prediction markets for mispricing opportunities.

**Core Logic:** When weather forecast implies a different probability than what the market price suggests → calculate Expected Value (EV) → auto-bet if EV exceeds threshold.

---

## 🚀 Quick Start

### 1. Clone & Install

Install [uv](https://docs.astral.sh/uv/) and choose the smallest dependency profile
that matches the execution mode:

```bash
git clone https://github.com/carabinshely/hermes_weatherbot.git
cd hermes_weatherbot

# Research, paper, resolution, and observation tooling only.
# No wallet, Web3, signing, or official SDK packages are installed.
uv sync --locked --no-dev

# Development and tests, still without live extras.
uv sync --locked --all-groups

# Explicit live-capable environment. Funded-wallet operation remains fail-closed.
uv sync --locked --no-dev --extra live
```

Run commands through the selected locked environment, for example:

```bash
uv run --no-dev python bot_v3.py scan --mode research
uv run --no-dev python bot_v3.py scan --mode paper
uv run --no-dev python bot_v3.py status --mode paper
uv run --no-dev python -m weatherbot.resolution --help
```

### 2. Configure

Copy the example env file and fill in your wallet credentials:

```bash
cp .env.example .env
```

Edit `.env` and never commit it:

```env
# Your Polygon private key (hex, without 0x prefix)
PK=your_polygon_private_key_here

# Your Polygon wallet address
WALLET=0xYourWalletAddressHere

# Signature type (0 = EOA)
SIG_TYPE=0

# Optional Visual Crossing key for the legacy resolution fallback
VC_KEY=your_visual_crossing_key

# Optional Telegram notifications
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

`config.json` is committed public configuration. Put trading parameters there, never credentials:

```json
{
  "mode": "disabled",
  "max_bet": 2.0,
  "min_ev": 0.10,
  "min_volume": 500,
  "scan_interval": 3600
}
```

### 3. Choose an execution mode

`config.json` defaults to `"mode": "research"`.

```bash
# Read-only market research; no wallet access or orders
python bot_v3.py scan --mode research

# Durable PAPER uses the same calibrated probability boundary. Before final
# #49/#48 activation this may intentionally create zero new model-backed entries.
python bot_v3.py scan --mode paper
python bot_v3.py run --mode paper
python bot_v3.py status --mode paper
python bot_v3.py resolve --mode paper

# Live mode is fail-closed and requires the live extra plus all three gates:
# 1. uv sync --locked --no-dev --extra live
# 2. config.json mode=live
# 3. --mode live
# 4. --confirm-live
uv run --no-dev --extra live python bot_v3.py scan --mode live --confirm-live
```

Research and paper modes do not require `PK`, `WALLET`, Web3, signing packages, or the
optional official SDK. A live command in a minimal environment exits with an actionable
installation error before credential or wallet access.

### 4. Start the configured mode

```bash
# Start the bot (runs in background)
./start_bot_v3.sh

# Stop the bot
./stop_bot_v3.sh
```

During #48B the public strategy entrypoint supports calibrated RESEARCH and durable PAPER simulation. Both remain fail-closed without accepted compatible calibration evidence; LIVE strategy execution remains explicitly disabled.

---

## 🧠 Core Math: Calibrated Residual Model

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

## 🌀 Auto-Evolution Learning System

This is a core strength of the Hermes Agent framework — the bot **learns from trading and auto-tunes**:

```
data/learning/
├── trade_log.json   # All trades: city, bucket, cost, outcome, pnl
└── model.json       # Learned parameters per city/bucket
```

**Adaptation Rules:**
- Winrate < 45% → Kelly fraction ×0.8, EV floor +10%
- Winrate > 55% + PnL > $2 → Kelly fraction ×1.1, EV floor −5%
- Per-city winrate tracking adjusts confidence per market
- Starts conservative (25% Kelly) → converges to optimal as data accumulates

---

## 📊 Architecture

```
ECMWF Weather Forecast API
        ↓
Hermes Agent (Autonomous Decision Engine)
    ├── Calibrated residual model → Model probability
    ├── calc_ev() → Expected Value Calculation
    ├── calc_kelly() → Optimal Bet Sizing
    └── Adaptive Learning → Auto Parameter Tuning
        ↓
Polymarket CLOB (On-chain, Polygon)
        ↓
Telegram (Real-time Notifications)
```

---

## 🛡️ Risk Management

| Parameter | Value | Purpose |
|---|---|---|
| Max bet | $2.00 | Per-trade exposure cap |
| Kelly fraction | 25% | 1/4 Kelly conservative |
| Min EV | 10%+ | Only trade positive EV |
| Min volume | $500 | Avoid illiquid markets |
| Max spread | 3% | Avoid high-slippage |
| Adaptive floor | 10-20% | Self-tuning from performance |

---

## 🔐 Full Automated Trading Flow

```
1. Fetch ECMWF forecast (D+0 ~ D+3)
2. Query Polymarket temperature bucket markets
3. Accepted calibrated residual model → model probability (fail closed if unavailable/incompatible)
4. Compare to market price → calculate EV
5. EV ≥ adaptive threshold → calculate Kelly bet size
6. Execute order on Polymarket CLOB (Polygon)
7. Record trade → update learning model
8. Telegram real-time notification
9. Repeat every 60 minutes
```

---

## 💡 Tech Stack

- **Framework:** Hermes Agent (autonomous learning + multi-platform)
- **Language:** Python 3.13
- **Trading:** [py_clob_client](https://github.com/polymarket/py-clob-client) — Polymarket CLOB
- **Weather:** ECMWF OpenMETAR / Open-Meteo API
- **Chain:** Polygon (Chain ID 137) — USDC.e stablecoin
- **Notifications:** Telegram Bot API
- **Learning:** Pure Python JSON persistence (zero DB dependency)

---

## ⚠️ Disclaimer

This bot trades real markets with real money. Past performance does not guarantee future results. Trade at your own risk. For educational and research purposes only.

---

*Built with 🐍 + Hermes Agent on Polygon — Autonomous Weather Prediction Trading.*
