#!/usr/bin/env python3
"""Fail CI if supported Hermes runtimes can transitively reach execution modules."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_START_MODULES = ("bot_v3", "weatherbot.producer")
PUBLIC_FORBIDDEN_PREFIXES = (
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
PAPER_START_MODULES = ("weatherbot.paper.cli",)
PAPER_FORBIDDEN_PREFIXES = (
    "bot_v3_legacy",
    "bot_v3_legacy_impl",
    "execution_modes",
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
    "approve_token",
    "get_clob",
    "get_w3",
)


def _is_forbidden(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


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


def _check_boundary(
    *,
    label: str,
    start_modules: tuple[str, ...],
    forbidden_prefixes: tuple[str, ...],
) -> tuple[list[str], int]:
    pending = list(start_modules)
    visited: set[str] = set()
    offenders: list[str] = []
    checked_paths: set[Path] = set()

    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        if _is_forbidden(module, forbidden_prefixes):
            offenders.append(f"{label}: {module}")
            continue
        path = _module_path(module)
        if path is None:
            continue
        checked_paths.add(path)
        for parent in _package_parents(module):
            if parent not in visited:
                pending.append(parent)
        for imported in _imports(path):
            if _is_forbidden(imported, forbidden_prefixes):
                offenders.append(f"{label}: {module} -> {imported}")
            elif _module_path(imported) is not None and imported not in visited:
                pending.append(imported)

    for path in sorted(checked_paths):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for symbol in FORBIDDEN_SYMBOLS:
            if symbol in text:
                offenders.append(f"{label}: {relative}: forbidden execution symbol {symbol}")

    return offenders, len(checked_paths)


def main() -> int:
    public_offenders, public_count = _check_boundary(
        label="PUBLIC",
        start_modules=PUBLIC_START_MODULES,
        forbidden_prefixes=PUBLIC_FORBIDDEN_PREFIXES,
    )
    paper_offenders, paper_count = _check_boundary(
        label="PAPER",
        start_modules=PAPER_START_MODULES,
        forbidden_prefixes=PAPER_FORBIDDEN_PREFIXES,
    )
    offenders = sorted(set(public_offenders + paper_offenders))
    if offenders:
        for offender in offenders:
            print(f"NON-EXECUTION VIOLATION: {offender}")
        return 1
    print(
        "non-execution boundaries OK "
        f"(public={public_count} repository modules, paper={paper_count} repository modules)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
