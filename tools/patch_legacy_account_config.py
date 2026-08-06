from __future__ import annotations

from pathlib import Path

path = Path("bot_v3.py")
content = path.read_text(encoding="utf-8")
old = '''_clob: UnsupportedTradingClient | None = None

def get_clob() -> UnsupportedTradingClient:
    global _clob
    if _clob is None:
        _clob = UnsupportedTradingClient()
    return _clob
'''
new = '''_clob: UnsupportedTradingClient | None = None

def get_clob() -> UnsupportedTradingClient:
    global _clob
    if _clob is None:
        _clob = UnsupportedTradingClient(
            signature_type=SIG_TYPE,
            wallet_address=WALLET or None,
        )
    return _clob
'''
count = content.count(old)
if count != 1:
    raise SystemExit(f"expected one get_clob marker, found {count}")
path.write_text(content.replace(old, new, 1), encoding="utf-8")
