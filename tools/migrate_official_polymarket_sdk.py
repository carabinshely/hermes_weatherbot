from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "pyproject.toml",
    '    "py-clob-client==0.34.6",\n',
    '    "polymarket-client==0.1.0b21",\n',
)

replace_once(
    "bot_v3.py",
    "bot_v2 strategy logic + py_clob_client on-chain order execution.\n",
    "bot_v2 strategy logic + official Polymarket public SDK boundary.\n"
    "Authenticated order execution remains fail-closed.\n",
)

replace_once(
    "bot_v3.py",
    "from py_clob_client.client import ClobClient\n"
    "from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType\n\n"
    "_clob: ClobClient = None\n\n"
    "def get_clob() -> ClobClient:\n"
    "    global _clob\n"
    "    if _clob is None:\n"
    "        _clob = ClobClient(\n"
    "            host=CLOB_HOST,\n"
    "            chain_id=CHAIN_ID,\n"
    "            key=PK,\n"
    "        )\n"
    "    return _clob\n",
    "from weatherbot.polymarket.legacy import (\n"
    "    MarketOrderArgs,\n"
    "    OrderArgs,\n"
    "    OrderType,\n"
    "    UnsupportedTradingClient,\n"
    ")\n\n"
    "_clob: UnsupportedTradingClient | None = None\n\n"
    "def get_clob() -> UnsupportedTradingClient:\n"
    "    global _clob\n"
    "    if _clob is None:\n"
    "        _clob = UnsupportedTradingClient()\n"
    "    return _clob\n",
)
