from __future__ import annotations

import ast
import math
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast


class LegacyMath(Protocol):
    def norm_cdf(self, x: float) -> float: ...
    def bucket_prob(
        self,
        forecast: float,
        t_low: float,
        t_high: float,
        sigma: float = 2.0,
    ) -> float: ...
    def calc_ev(self, p: float, price: float) -> float: ...
    def calc_kelly(self, p: float, price: float) -> float: ...
    def bet_size(self, kelly: float) -> float: ...
    def parse_temp_range(self, question: str) -> tuple[float, float] | None: ...
    def in_bucket(self, forecast: float, t_low: float, t_high: float) -> bool: ...


_REQUIRED_FUNCTIONS = {
    "bet_size",
    "bucket_prob",
    "calc_ev",
    "calc_kelly",
    "in_bucket",
    "norm_cdf",
    "parse_temp_range",
}


def load_legacy_math(path: Path) -> LegacyMath:
    """Load selected pure functions without importing the side-effectful legacy module."""
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions: list[ast.stmt] = []
    found: set[str] = set()

    for node in module.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in _REQUIRED_FUNCTIONS
        ):
            functions.append(node)
            found.add(node.name)

    missing = _REQUIRED_FUNCTIONS - found
    if missing:
        raise AssertionError(f"Missing expected legacy functions: {sorted(missing)}")

    isolated = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(isolated)

    namespace: dict[str, Any] = {
        "KELLY_FRAC": 0.25,
        "MAX_BET": 2.0,
        "math": math,
        "re": re,
    }
    exec(compile(isolated, str(path), "exec"), namespace)
    loaded = SimpleNamespace(**{name: namespace[name] for name in _REQUIRED_FUNCTIONS})
    return cast(LegacyMath, loaded)
