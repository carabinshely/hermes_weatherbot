from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from weatherbot.dependencies import (
    LIVE_INSTALL_COMMAND,
    LiveDependenciesUnavailable,
    missing_live_dependencies,
    require_live_dependencies,
)

ROOT = Path(__file__).resolve().parents[1]
LIVE_DISTRIBUTIONS = {
    "eth-account==0.13.7",
    "polymarket-client==0.1.0b21",
    "web3==7.16.0",
}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module.split(".", 1)[0])
    return imports


def test_base_runtime_excludes_wallet_and_sdk_distributions() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert set(project["dependencies"]) == {
        "python-dotenv==1.2.2",
        "requests==2.34.2",
    }
    assert set(project["optional-dependencies"]["live"]) == LIVE_DISTRIBUTIONS


def test_safe_entry_points_do_not_import_live_packages_at_module_load() -> None:
    forbidden = {"eth_account", "polymarket", "web3"}
    assert _top_level_imports(ROOT / "bot_v3.py").isdisjoint(forbidden)
    assert _top_level_imports(
        ROOT / "weatherbot" / "polymarket" / "read_client.py"
    ).isdisjoint(forbidden)


def test_missing_live_dependencies_are_reported_without_importing() -> None:
    available = {"eth_account"}

    def finder(name: str) -> object | None:
        return object() if name in available else None

    assert missing_live_dependencies(finder) == (
        "polymarket-client",
        "web3",
    )
    with pytest.raises(LiveDependenciesUnavailable) as error:
        require_live_dependencies(finder)
    message = str(error.value)
    assert "polymarket-client, web3" in message
    assert LIVE_INSTALL_COMMAND in message


def test_complete_live_profile_passes_without_importing() -> None:
    def finder(_name: str) -> object:
        return object()

    require_live_dependencies(finder)
