"""Optional dependency profiles and fail-closed runtime checks."""

from __future__ import annotations

from collections.abc import Callable
from importlib.util import find_spec
from types import ModuleType

LIVE_IMPORTS: tuple[tuple[str, str], ...] = (
    ("eth_account", "eth-account"),
    ("polymarket", "polymarket-client"),
    ("web3", "web3"),
)
LIVE_INSTALL_COMMAND = "uv sync --locked --extra live"


class LiveDependenciesUnavailable(RuntimeError):
    """Raised before a live-only operation when its optional packages are absent."""


def missing_live_dependencies(
    finder: Callable[[str], object | None] = find_spec,
) -> tuple[str, ...]:
    """Return missing live distribution names without importing their modules."""
    return tuple(
        distribution
        for import_name, distribution in LIVE_IMPORTS
        if finder(import_name) is None
    )


def require_live_dependencies(
    finder: Callable[[str], object | None] = find_spec,
) -> None:
    """Fail with one actionable message before importing wallet or SDK packages."""
    missing = missing_live_dependencies(finder)
    if missing:
        joined = ", ".join(missing)
        raise LiveDependenciesUnavailable(
            f"live mode requires optional dependencies: {joined}. "
            f"Install them with `{LIVE_INSTALL_COMMAND}`."
        )


def require_module_attribute(
    module: ModuleType,
    attribute: str,
) -> object:
    """Return a required optional-module attribute with a stable failure message."""
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise LiveDependenciesUnavailable(
            f"optional module {module.__name__!r} does not expose {attribute!r}; "
            f"reinstall with `{LIVE_INSTALL_COMMAND}`"
        ) from exc
