from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:120]!r}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


Path("execution_modes.py").write_text(
    '''from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar


class ExecutionMode(StrEnum):
    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class ModeConfigurationError(ValueError):
    """Raised when execution mode configuration is unsafe or ambiguous."""


class LiveExecutionBlocked(RuntimeError):
    """Raised when a live-only operation is requested outside confirmed live mode."""


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    mode: ExecutionMode
    configured_mode: ExecutionMode
    live_confirmed: bool = False

    @property
    def requires_wallet(self) -> bool:
        return self.mode is ExecutionMode.LIVE

    @property
    def label(self) -> str:
        return self.mode.value.upper()


def parse_mode(value: object, *, source: str) -> ExecutionMode:
    if not isinstance(value, str) or not value.strip():
        raise ModeConfigurationError(f"{source} must be research, paper, or live")
    normalized = value.strip().lower()
    try:
        return ExecutionMode(normalized)
    except ValueError as exc:
        raise ModeConfigurationError(
            f"invalid {source} {value!r}; expected research, paper, or live"
        ) from exc


def resolve_execution_context(
    *,
    configured_mode: object,
    cli_mode: str | None,
    confirm_live: bool,
) -> ExecutionContext:
    configured = parse_mode(configured_mode, source="config mode")
    selected = configured if cli_mode is None else parse_mode(cli_mode, source="CLI mode")

    if confirm_live and selected is not ExecutionMode.LIVE:
        raise ModeConfigurationError("--confirm-live is valid only with --mode live")

    if selected is ExecutionMode.LIVE:
        if configured is not ExecutionMode.LIVE:
            raise ModeConfigurationError("live mode requires config.json mode='live'")
        if cli_mode is None or parse_mode(cli_mode, source="CLI mode") is not ExecutionMode.LIVE:
            raise ModeConfigurationError(
                "live mode must be requested explicitly with --mode live"
            )
        if not confirm_live:
            raise ModeConfigurationError(
                "live mode requires the additional --confirm-live flag"
            )

    return ExecutionContext(
        mode=selected,
        configured_mode=configured,
        live_confirmed=selected is ExecutionMode.LIVE and confirm_live,
    )


def require_live(context: ExecutionContext, *, operation: str) -> None:
    if context.mode is not ExecutionMode.LIVE or not context.live_confirmed:
        raise LiveExecutionBlocked(f"{operation} is blocked in {context.mode.value} mode")


T = TypeVar("T")


def run_live_operation(
    context: ExecutionContext,
    *,
    operation: str,
    callback: Callable[[], T],
) -> T:
    """Run a callback only after the confirmed-live gate succeeds."""
    require_live(context, operation=operation)
    return callback()
''',
    encoding="utf-8",
)

bot = Path("bot_v3.py")
content = bot.read_text(encoding="utf-8")
content = content.replace("import re\n", "import argparse\nimport re\n", 1)
content = content.replace(
    "from runtime_security import credential_status_line\n",
    '''from execution_modes import (
    ExecutionContext,
    ExecutionMode,
    LiveExecutionBlocked,
    ModeConfigurationError,
    require_live,
    resolve_execution_context,
    run_live_operation,
)
from runtime_security import credential_status_line
''',
    1,
)

replace_target = '''def tg_signal(city: str, horizon: str, date: str, bucket_label: str,
              forecast_temp: float, entry_price: float, cost: float,
              ev: float, kelly: float, success: bool, reason: str = ""):
    """Send a trade signal notification to Telegram."""
'''
replace_value = '''def tg_signal(city: str, horizon: str, date: str, bucket_label: str,
              forecast_temp: float, entry_price: float, cost: float,
              ev: float, kelly: float, success: bool, mode: ExecutionMode,
              reason: str = ""):
    """Send a trade signal notification to Telegram."""
    mode_header = f"🔒 <b>{mode.value.upper()} MODE</b>\\n"
'''
if replace_target not in content:
    raise SystemExit("tg_signal signature marker not found")
content = content.replace(replace_target, replace_value, 1)
needle = '        msg = (\n            f"📍 <b>{city} {horizon}</b> — {date}\\n"'
replacement = '        msg = (\n            mode_header\n            + f"📍 <b>{city} {horizon}</b> — {date}\\n"'
if content.count(needle) != 2:
    raise SystemExit(f"expected two Telegram message markers, found {content.count(needle)}")
content = content.replace(needle, replacement, 2)

summary_start = content.index("def tg_scan_summary(")
summary_end = content.index(
    "\n# =============================================================================\n# APPROVAL CHECK", summary_start
)
content = content[:summary_start] + '''def tg_scan_summary(new_trades: int, errors: int, balance: float | None, cities: int,
                     mode: ExecutionMode, observed_signals: int = 0,
                     paper_candidates: int = 0, top_signals: list = None,
                     open_positions: list = None):
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
            pnl_str = f"${pos.get('pnl', 0):.2f}" if pos.get('pnl') else "pending"
            entry = pos.get('entry_price', 0)
            cost = pos.get('cost', 0)
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

    send_telegram("\\n".join(lines))
''' + content[summary_end:]

old_scan_intro = '''def scan_and_trade():
    """
    One-shot scan: check all cities for trade signals and execute real orders.
    Returns (new_trades, errors).
    """
    now = datetime.now(timezone.utc)
    state = load_state()
    balance = get_usdc_balance(WALLET)
    if balance != state.get("balance"):
        state["balance"] = balance
        save_state(state)

    print(f"\\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — Live Mode{C.RESET}")
    print("=" * 60)
    print(f"  Wallet:       {WALLET[:8]}...{WALLET[-4:]}")
    print(f"  USDC.e:       ${balance:.4f}")
    print(f"  POL balance:  {get_pol_balance(WALLET):.4f} POL")
    print(f"  Max bet:      ${MAX_BET} | Min EV: {MIN_EV*100:.0f}%")
    print()

    new_trades = 0
    errors = []
'''
new_scan_intro = '''def scan_and_trade(context: ExecutionContext):
    """Scan markets under an explicit execution mode."""
    now = datetime.now(timezone.utc)
    is_live = context.mode is ExecutionMode.LIVE
    state = load_state() if is_live else {"balance": 0.0, "total_trades": 0}
    balance = None
    if is_live:
        balance = get_usdc_balance(WALLET)
        if balance != state.get("balance"):
            state["balance"] = balance
            save_state(state)

    print(f"\\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — {context.label} MODE{C.RESET}")
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
    print(f"  Max bet:      ${MAX_BET} | Min EV: {MIN_EV*100:.0f}%")
    print()

    new_trades = 0
    observed_signals = 0
    paper_candidates = 0
    errors = []
'''
if old_scan_intro not in content:
    raise SystemExit("scan intro marker not found")
content = content.replace(old_scan_intro, new_scan_intro, 1)

execution_marker = "                # --- EXECUTE REAL ORDER ---\n"
mode_guard = '''                if context.mode is ExecutionMode.RESEARCH:
                    observed_signals += 1
                    info("  [RESEARCH] signal observed; no order or state mutation")
                    continue
                if context.mode is ExecutionMode.PAPER:
                    paper_candidates += 1
                    info("  [PAPER] candidate only; simulated fills are implemented in #27")
                    continue

                require_live(context, operation="place order")
                assert balance is not None

                # --- EXECUTE REAL ORDER ---
'''
if execution_marker not in content:
    raise SystemExit("execution marker not found")
content = content.replace(execution_marker, mode_guard, 1)

old_order_call = '''                result = place_buy_order(
                    market_id=best_signal["market_id"],
                    token_id=best_signal["token_id"],
                    price=best_signal["entry_price"],
                    shares=best_signal["shares"],
                    private_key=PK,
                    wallet=WALLET,
                )
'''
new_order_call = '''                result = run_live_operation(
                    context,
                    operation="place order",
                    callback=lambda: place_buy_order(
                        market_id=best_signal["market_id"],
                        token_id=best_signal["token_id"],
                        price=best_signal["entry_price"],
                        shares=best_signal["shares"],
                        private_key=PK,
                        wallet=WALLET,
                    ),
                )
'''
if old_order_call not in content:
    raise SystemExit("live order call marker not found")
content = content.replace(old_order_call, new_order_call, 1)
content = content.replace("                        success=True,\n", "                        success=True, mode=context.mode,\n", 1)
content = content.replace(
    "                        success=False, reason=result.get(\"reason\", \"unknown\"),\n",
    "                        success=False, mode=context.mode, reason=result.get(\"reason\", \"unknown\"),\n",
    1,
)

old_state_tail = '''    # Open positions
    markets = load_all_markets()
    open_positions = [
        m for m in markets
        if m.get("position") and m["position"].get("status") == "open"
    ]

    # Save updated balance
    state["balance"] = round(balance, 4)
    save_state(state)
'''
new_state_tail = '''    # Live positions and state are inaccessible to non-live modes.
    open_positions = []
    if is_live:
        markets = load_all_markets()
        open_positions = [
            m for m in markets
            if m.get("position") and m["position"].get("status") == "open"
        ]
        assert balance is not None
        state["balance"] = round(balance, 4)
        save_state(state)
'''
if old_state_tail not in content:
    raise SystemExit("state tail marker not found")
content = content.replace(old_state_tail, new_state_tail, 1)

old_report = '''    print(f"  New trades: {C.GREEN}{new_trades}{C.RESET}")
    print(f"  Errors:     {len(errors)}")
    print(f"  Balance:    ${balance:.4f}")
'''
new_report = '''    print(f"  New trades: {C.GREEN}{new_trades}{C.RESET}")
    print(f"  Signals:    {observed_signals}")
    print(f"  Paper candidates: {paper_candidates}")
    print(f"  Errors:     {len(errors)}")
    if balance is None:
        print("  Wallet:     disabled")
    else:
        print(f"  Balance:    ${balance:.4f}")
'''
if old_report not in content:
    raise SystemExit("scan report marker not found")
content = content.replace(old_report, new_report, 1)

old_summary_call = '''    tg_scan_summary(new_trades=new_trades, errors=len(errors),
                    balance=balance, cities=len(LOCATIONS),
                    top_signals=top_signals,
                    open_positions=open_positions)
'''
new_summary_call = '''    tg_scan_summary(new_trades=new_trades, errors=len(errors),
                    balance=balance, cities=len(LOCATIONS), mode=context.mode,
                    observed_signals=observed_signals,
                    paper_candidates=paper_candidates,
                    top_signals=top_signals,
                    open_positions=open_positions)
'''
if old_summary_call not in content:
    raise SystemExit("summary call marker not found")
content = content.replace(old_summary_call, new_summary_call, 1)

old_status_header = '''def show_status():
    """Show current balance, positions, and open orders."""
    balance = get_usdc_balance(WALLET)
'''
new_status_header = '''def show_status(context: ExecutionContext):
    """Show status without crossing the selected mode boundary."""
    if context.mode is not ExecutionMode.LIVE:
        print(f"\\n{C.BOLD}{C.CYAN}📊 Bot v3 — {context.label} MODE{C.RESET}")
        print("=" * 60)
        print("  Wallet access: disabled")
        if context.mode is ExecutionMode.PAPER:
            print("  Paper ledger:  pending implementation in #27")
        print(f"{'=' * 60}\\n")
        return

    require_live(context, operation="live status")
    balance = get_usdc_balance(WALLET)
'''
if old_status_header not in content:
    raise SystemExit("status header marker not found")
content = content.replace(old_status_header, new_status_header, 1)
content = content.replace(
    '    print(f"\\n{C.BOLD}{C.CYAN}📊 Bot v3 — Status{C.RESET}")\n',
    '    print(f"\\n{C.BOLD}{C.CYAN}📊 Bot v3 — {context.label} MODE{C.RESET}")\n',
    1,
)

content = content.replace("def run_loop():\n", "def run_loop(context: ExecutionContext):\n", 1)
content = content.replace(
    '    print(f"\\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — LIVE{C.RESET}")\n',
    '    print(f"\\n{C.BOLD}{C.CYAN}🌤  Weather Trading Bot v3 — {context.label} MODE{C.RESET}")\n',
    1,
)
old_wallet_line = '    print(f"  Wallet:    {WALLET[:8]}...{WALLET[-4:]}")\n'
new_wallet_line = '''    print(f"  Mode:     {context.label}")
    if context.mode is ExecutionMode.LIVE:
        print(f"  Wallet:   {WALLET[:8]}...{WALLET[-4:]}")
    else:
        print("  Wallet:   disabled")
'''
if old_wallet_line not in content:
    raise SystemExit("run-loop wallet marker not found")
content = content.replace(old_wallet_line, new_wallet_line, 1)
old_approvals = '''    # Check approvals on startup
    ok("Checking approvals...")
    ensure_approvals()
'''
new_approvals = '''    if context.mode is ExecutionMode.LIVE:
        require_live(context, operation="token approval")
        ok("Checking approvals...")
        ensure_approvals()
    else:
        skip("Live approvals disabled by execution mode")
'''
if old_approvals not in content:
    raise SystemExit("approval marker not found")
content = content.replace(old_approvals, new_approvals, 1)
content = content.replace(
    "                new_trades, errors = scan_and_trade()\n",
    "                new_trades, errors = scan_and_trade(context)\n",
    1,
)

cli_start = content.index('if __name__ == "__main__":')
content = content[:cli_start] + '''def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weather-market bot")
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=("scan", "run", "status", "cancel"),
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
'''
bot.write_text(content, encoding="utf-8")

config_path = Path("config.json")
config = json.loads(config_path.read_text(encoding="utf-8"))
config["mode"] = "research"
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

Path("tests/test_execution_modes.py").write_text(
    '''from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from execution_modes import (
    ExecutionContext,
    ExecutionMode,
    LiveExecutionBlocked,
    ModeConfigurationError,
    parse_mode,
    require_live,
    resolve_execution_context,
    run_live_operation,
)


@pytest.mark.parametrize("value", [None, "", "disabled", "automatic", 1])
def test_invalid_modes_fail_closed(value: object) -> None:
    with pytest.raises(ModeConfigurationError):
        parse_mode(value, source="test mode")


def test_research_is_safe_default_from_config() -> None:
    context = resolve_execution_context(
        configured_mode="research", cli_mode=None, confirm_live=False
    )
    assert context.mode is ExecutionMode.RESEARCH
    assert not context.requires_wallet
    assert context.label == "RESEARCH"


def test_cli_can_safely_downgrade_live_configuration() -> None:
    context = resolve_execution_context(
        configured_mode="live", cli_mode="research", confirm_live=False
    )
    assert context.mode is ExecutionMode.RESEARCH


@pytest.mark.parametrize(
    ("configured_mode", "cli_mode", "confirm_live"),
    [
        ("research", "live", True),
        ("live", None, True),
        ("live", "live", False),
        ("paper", "paper", True),
    ],
)
def test_live_mode_requires_all_independent_gates(
    configured_mode: str, cli_mode: str | None, confirm_live: bool
) -> None:
    with pytest.raises(ModeConfigurationError):
        resolve_execution_context(
            configured_mode=configured_mode,
            cli_mode=cli_mode,
            confirm_live=confirm_live,
        )


def test_live_mode_requires_config_cli_and_confirmation() -> None:
    context = resolve_execution_context(
        configured_mode="live", cli_mode="live", confirm_live=True
    )
    assert context.mode is ExecutionMode.LIVE
    assert context.live_confirmed
    assert context.requires_wallet


@pytest.mark.parametrize("mode", [ExecutionMode.RESEARCH, ExecutionMode.PAPER])
def test_non_live_gate_never_calls_live_callback(mode: ExecutionMode) -> None:
    context = ExecutionContext(mode=mode, configured_mode=mode)
    calls: list[str] = []

    with pytest.raises(LiveExecutionBlocked):
        run_live_operation(
            context,
            operation="test live call",
            callback=lambda: calls.append("live"),
        )

    assert calls == []
    with pytest.raises(LiveExecutionBlocked):
        require_live(context, operation="test live call")


@pytest.mark.parametrize("mode", ["research", "paper"])
def test_non_live_status_runs_without_wallet_credentials(mode: str) -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "status", "--mode", mode],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"Execution mode: {mode.upper()}" in completed.stdout
    assert "Wallet access: disabled" in completed.stdout


def test_default_status_is_research_and_wallet_free() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "status"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Execution mode: RESEARCH" in completed.stdout
    assert "Wallet access: disabled" in completed.stdout


def test_non_live_cancel_is_blocked_before_live_client_access() -> None:
    environment = os.environ.copy()
    environment.pop("PK", None)
    environment.pop("WALLET", None)
    completed = subprocess.run(
        [sys.executable, "bot_v3.py", "cancel", "--mode", "research"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    assert "order cancellation is blocked in research mode" in completed.stderr
''',
    encoding="utf-8",
)

pyproject = Path("pyproject.toml")
project = pyproject.read_text(encoding="utf-8")
project = project.replace(
    'include = ["runtime_security.py", "tests"]',
    'include = ["execution_modes.py", "runtime_security.py", "tests"]',
)
project = project.replace(
    'source = ["runtime_security", "tests"]',
    'source = ["execution_modes", "runtime_security", "tests"]',
)
pyproject.write_text(project, encoding="utf-8")

readme = Path("README.md")
readme_content = readme.read_text(encoding="utf-8")
marker = "### 3. Start Trading\n\n"
mode_docs = '''### 3. Choose an execution mode

`config.json` defaults to `"mode": "research"`.

```bash
# Read-only market research; no wallet access or orders
python bot_v3.py scan --mode research

# Paper-mode candidate generation; simulated fills arrive in #27
python bot_v3.py scan --mode paper

# Live mode is fail-closed and requires all three gates:
# 1. config.json mode=live
# 2. --mode live
# 3. --confirm-live
python bot_v3.py scan --mode live --confirm-live
```

Research and paper modes do not require `PK` or `WALLET`.

### 4. Start the configured mode

'''
if marker not in readme_content:
    raise SystemExit("README start marker not found")
readme.write_text(readme_content.replace(marker, mode_docs, 1), encoding="utf-8")
