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
    "bot_v3.py",
    '''    python bot_v3.py status       # Show open positions + balance
    python bot_v3.py cancel       # Cancel all open orders
''',
    '''    python bot_v3.py status       # Show open positions + balance
    python bot_v3.py resolve      # Resolve and settle pending ledger positions
    python bot_v3.py cancel       # Cancel all open orders
''',
)
replace_once(
    "bot_v3.py",
    '''from weatherbot.markets import (
''',
    '''from weatherbot.resolution import run_resolution_cycle as resolve_ledger_positions
from weatherbot.markets import (
''',
)
replace_once(
    "bot_v3.py",
    '''SCAN_INTERVAL = _cfg.get("scan_interval", 3600)

# --- CLOB ---
''',
    '''SCAN_INTERVAL = _cfg.get("scan_interval", 3600)
_ledger_config_path = Path(_cfg.get("ledger_path", "state/ledger.sqlite3"))
LEDGER_PATH = (
    _ledger_config_path
    if _ledger_config_path.is_absolute()
    else BOT_DIR / _ledger_config_path
)

# --- CLOB ---
''',
)
replace_once(
    "bot_v3.py",
    '''MONITOR_INTERVAL = 600  # 10 minutes between monitor cycles


def run_loop(context: ExecutionContext):
''',
    '''MONITOR_INTERVAL = 600  # 10 minutes between monitor cycles


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
''',
)
replace_once(
    "bot_v3.py",
    '''        else:
            print(f"[{now_str}] Monitoring...")
            time.sleep(MONITOR_INTERVAL)
''',
    '''        else:
            print(f"[{now_str}] Monitoring...")
            try:
                run_resolution_monitor_cycle()
            except Exception as exc:
                warn(f"Resolution monitor error: {exc}")
            time.sleep(MONITOR_INTERVAL)
''',
)
replace_once(
    "bot_v3.py",
    '        choices=("scan", "run", "status", "cancel"),\n',
    '        choices=("scan", "run", "status", "resolve", "cancel"),\n',
)
replace_once(
    "bot_v3.py",
    '''    elif args.command == "status":
        show_status(context)
    elif args.command == "cancel":
''',
    '''    elif args.command == "status":
        show_status(context)
    elif args.command == "resolve":
        run_resolution_monitor_cycle()
    elif args.command == "cancel":
''',
)
