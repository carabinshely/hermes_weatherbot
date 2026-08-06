from __future__ import annotations

import ast
from pathlib import Path

BANNED_ROOTS = {
    "eth_account",
    "py_clob_client",
    "requests",
    "web3",
}


def imported_roots(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def test_market_contract_package_has_no_network_wallet_or_sdk_imports() -> None:
    market_files = sorted(Path("weatherbot/markets").glob("*.py"))
    assert market_files
    violations = {
        str(path): sorted(imported_roots(path) & BANNED_ROOTS)
        for path in market_files
        if imported_roots(path) & BANNED_ROOTS
    }
    assert not violations


def test_market_contract_package_does_not_import_legacy_bot() -> None:
    for path in Path("weatherbot/markets").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "bot_v1" not in source
        assert "bot_v2" not in source
        assert "bot_v3" not in source
