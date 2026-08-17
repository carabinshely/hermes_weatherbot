#!/usr/bin/env python3
"""Fail CI if the public producer can transitively reach execution/PAPER modules."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START_MODULES = ("bot_v3", "weatherbot.producer")
FORBIDDEN_PREFIXES = (
    "bot_v3_legacy",
    "bot_v3_legacy_impl",
    "execution_modes",
    "weatherbot.paper",
    "weatherbot.dependencies",
    "weatherbot.polymarket",
    "web3",
    "eth_account",
    "polymarket",
)
FORBIDDEN_SYMBOLS = (
    "PK",
    "WALLET",
    "SIG_TYPE",
    "require_live",
    "run_live_operation",
    "send_raw_transaction",
    "place_buy_order",
    "cancel_all_orders",
    "ensure_approvals",
)


def _is_forbidden(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)


def _module_path(module: str) -> Path | None:
    parts = module.split(".")
    file_path = ROOT.joinpath(*parts).with_suffix(".py")
    if file_path.exists():
        return file_path
    package_path = ROOT.joinpath(*parts, "__init__.py")
    if package_path.exists():
        return package_path
    return None


def _package_parents(module: str) -> tuple[str, ...]:
    parts = module.split(".")
    return tuple(".".join(parts[:index]) for index in range(1, len(parts)))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def main() -> int:
    pending = list(START_MODULES)
    visited: set[str] = set()
    offenders: list[str] = []
    checked_paths: set[Path] = set()

    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        if _is_forbidden(module):
            offenders.append(module)
            continue
        path = _module_path(module)
        if path is None:
            continue
        checked_paths.add(path)
        for parent in _package_parents(module):
            if parent not in visited:
                pending.append(parent)
        for imported in _imports(path):
            if _is_forbidden(imported):
                offenders.append(f"{module} -> {imported}")
            elif _module_path(imported) is not None and imported not in visited:
                pending.append(imported)

    for path in sorted(checked_paths):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for symbol in FORBIDDEN_SYMBOLS:
            if symbol in text:
                offenders.append(f"{relative}: forbidden public symbol {symbol}")

    if offenders:
        for offender in sorted(set(offenders)):
            print(f"PUBLIC NON-EXECUTION VIOLATION: {offender}")
        return 1
    print(f"public non-execution boundary OK ({len(checked_paths)} repository modules checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
