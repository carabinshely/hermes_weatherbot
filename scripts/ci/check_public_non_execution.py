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


def _current_package(module: str, path: Path) -> tuple[str, ...]:
    parts = tuple(module.split("."))
    return parts if path.name == "__init__.py" else parts[:-1]


def _resolve_from_import(module: str, path: Path, node: ast.ImportFrom) -> set[str]:
    if node.level == 0:
        if node.module is None:
            return set()
        base = node.module
    else:
        package = _current_package(module, path)
        parents_up = node.level - 1
        if parents_up > len(package):
            return set()
        anchor = package[: len(package) - parents_up]
        suffix = tuple(node.module.split(".")) if node.module else ()
        parts = anchor + suffix
        if not parts:
            return set()
        base = ".".join(parts)

    result = {base}
    # ``from package import submodule`` may load the submodule even when package
    # ``__init__`` does not import it. Include repository-backed alias candidates so
    # the static graph mirrors Python's import behavior closely enough for this guard.
    for alias in node.names:
        if alias.name == "*":
            continue
        candidate = f"{base}.{alias.name}"
        if _module_path(candidate) is not None:
            result.add(candidate)
    return result


def _imports(module: str, path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.update(_resolve_from_import(module, path, node))
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
        for imported in _imports(module, path):
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
