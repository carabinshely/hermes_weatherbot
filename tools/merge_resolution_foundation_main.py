from __future__ import annotations

from pathlib import Path

path = Path("bot_v3.py")
content = path.read_text(encoding="utf-8")

old_description = "bot_v2 strategy logic + py_clob_client on-chain order execution."
new_description = (
    "bot_v2 strategy logic + official Polymarket public SDK boundary.\n"
    "Authenticated order execution remains fail-closed."
)
if content.count(old_description) != 1:
    raise SystemExit(f"legacy description marker count: {content.count(old_description)}")
content = content.replace(old_description, new_description, 1)

old_client = '''from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType

_clob: ClobClient = None


def get_clob() -> ClobClient:
    global _clob
    if _clob is None:
        _clob = ClobClient(
            host=CLOB_HOST,
            chain_id=CHAIN_ID,
            key=PK,
        )
    return _clob
'''
new_client = '''from weatherbot.polymarket.legacy import (
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
'''
if content.count(old_client) != 1:
    raise SystemExit(f"legacy client marker count: {content.count(old_client)}")
content = content.replace(old_client, new_client, 1)
path.write_text(content, encoding="utf-8")
