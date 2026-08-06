from __future__ import annotations

import pytest

from weatherbot.dependencies import LiveDependenciesUnavailable, require_live_dependencies


def test_live_dependency_error_is_actionable_and_precedes_imports() -> None:
    attempts: list[str] = []

    def finder(name: str) -> None:
        attempts.append(name)
        return None

    with pytest.raises(LiveDependenciesUnavailable) as error:
        require_live_dependencies(finder)

    assert attempts == ["eth_account", "polymarket", "web3"]
    assert "uv sync --locked --extra live" in str(error.value)
