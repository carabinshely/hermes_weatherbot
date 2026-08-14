from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if old not in source:
        raise SystemExit(f"patch anchor not found in {path}: {old[:100]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "bot_v3.py",
    '''def run_loop(context: ExecutionContext):
    """Run repeated calibrated RESEARCH scans; execution modes remain disabled."""
    if context.mode is not ExecutionMode.RESEARCH:
        return _blocked_strategy_scan(context)
    while True:
        scan_and_trade(context)
        time.sleep(SCAN_INTERVAL)
''',
    '''def run_loop(context: ExecutionContext):
    """Run calibrated RESEARCH scans while retaining mechanical resolution monitoring."""
    if context.mode is not ExecutionMode.RESEARCH:
        return _blocked_strategy_scan(context)

    last_full_scan = 0.0
    while True:
        now_ts = time.time()
        if now_ts - last_full_scan >= SCAN_INTERVAL:
            scan_and_trade(context)
            last_full_scan = time.time()
            continue

        try:
            run_resolution_monitor_cycle()
        except Exception as exc:
            _legacy.warn(f"Resolution monitor error: {exc}")
        time.sleep(_legacy.MONITOR_INTERVAL)
''',
)

gate = Path("tests/forecasting/test_calibrated_scanner_gate.py")
gate_source = gate.read_text(encoding="utf-8")
gate_source += '''


def test_research_run_loop_retains_resolution_monitoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    clock = iter((10000.0, 10000.0, 10001.0))

    monkeypatch.setattr(bot_v3.time, "time", lambda: next(clock))

    def record_scan(_context: ExecutionContext) -> tuple[int, list[str]]:
        events.append("scan")
        return 0, []

    def record_resolution(*_args: object, **_kwargs: object) -> None:
        events.append("resolve")

    class StopLoop(Exception):
        pass

    def stop_after_monitor(_seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(bot_v3, "scan_and_trade", record_scan)
    monkeypatch.setattr(bot_v3, "run_resolution_monitor_cycle", record_resolution)
    monkeypatch.setattr(bot_v3.time, "sleep", stop_after_monitor)

    with pytest.raises(StopLoop):
        bot_v3.run_loop(_context(ExecutionMode.RESEARCH))

    assert events == ["scan", "resolve"]
'''
gate.write_text(gate_source, encoding="utf-8")

readme = Path("README.md")
readme_source = readme.read_text(encoding="utf-8")
replace_marker = "---\n![alt text](image-1.png)\n"
status_notice = '''---

> **Current #48A activation status:** calibrated strategy scanning is **RESEARCH-only**. `bot_v3.py scan/run --mode paper` and LIVE strategy `scan/run` are intentionally disabled and fail closed. Existing PAPER ledger/status/reset/resolution mechanics remain available. Historical target/legacy architecture sections below do not imply that automated trading is currently enabled.

![alt text](image-1.png)
'''
if replace_marker not in readme_source:
    raise SystemExit("README status notice anchor not found")
readme_source = readme_source.replace(replace_marker, status_notice, 1)
readme_source = readme_source.replace(
    "uv run --no-dev python bot_v3.py scan --mode paper\n",
    "uv run --no-dev python bot_v3.py status --mode paper\n",
    1,
)
readme_source = readme_source.replace(
    '''# Paper-mode candidate generation; simulated fills arrive in #27
python bot_v3.py scan --mode paper
''',
    '''# PAPER strategy scan/run are intentionally disabled during #48A.
# Administrative PAPER commands remain available:
python bot_v3.py status --mode paper
python bot_v3.py resolve --mode paper
''',
    1,
)
readme_source = readme_source.replace(
    "That's it! The bot will continuously scan markets and trade automatically.\n",
    "During #48A the public strategy entrypoint is RESEARCH-only; PAPER/LIVE strategy execution remains explicitly disabled until the later integration gate.\n",
    1,
)
readme.write_text(readme_source, encoding="utf-8")

paper_docs = Path("docs/paper-trading.md")
paper_source = paper_docs.read_text(encoding="utf-8")
intro = "# PAPER trading\n\n"
intro_notice = '''# PAPER trading

> **#48A integration state:** the PAPER simulation subsystem remains implemented, testable, and administratively accessible, but `bot_v3.py scan --mode paper` and `bot_v3.py run --mode paper` are intentionally disabled until the calibrated strategy-to-PAPER integration is reviewed. This phase does not generate new PAPER candidates from the public scanner.

'''
if intro not in paper_source:
    raise SystemExit("paper docs intro anchor not found")
paper_source = paper_source.replace(intro, intro_notice, 1)
commands_old = '''## Commands

Run one PAPER scan:

```bash
python bot_v3.py scan --mode paper
```

Run continuously:

```bash
python bot_v3.py run --mode paper
```

Show PAPER account status:
'''
commands_new = '''## Commands

### Strategy scan gate during #48A

The public PAPER strategy commands are intentionally disabled in this phase:

```bash
python bot_v3.py scan --mode paper
python bot_v3.py run --mode paper
```

Both exit with status 2 before strategy/calibration/network work. They are **not** the supported way to generate PAPER candidates until the remaining #48 integration is completed. The deterministic PAPER service, fixtures, ledger, recovery, valuation, risk, and settlement tests remain available.

Administrative/mechanical PAPER commands remain supported. Show PAPER account status:
'''
if commands_old not in paper_source:
    raise SystemExit("paper docs command anchor not found")
paper_source = paper_source.replace(commands_old, commands_new, 1)
paper_docs.write_text(paper_source, encoding="utf-8")
