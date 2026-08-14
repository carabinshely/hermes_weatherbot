from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVED_IMPORT = "py" + "_clob_client"
ARCHIVED_PACKAGE = "py" + "-clob-client"
OFFICIAL_PACKAGE = "polymarket-client==0.1.0b21"


def test_runtime_sources_do_not_import_archived_client() -> None:
    runtime_sources = [
        ROOT / "bot_v1.py",
        ROOT / "bot_v2.py",
        ROOT / "bot_v3.py",
        ROOT / "bot_v3_legacy.py",
        *(ROOT / "weatherbot").rglob("*.py"),
    ]
    offenders = [
        str(path.relative_to(ROOT))
        for path in runtime_sources
        if ARCHIVED_IMPORT in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_official_sdk_is_live_optional_not_base_runtime() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]

    assert ARCHIVED_PACKAGE not in str(pyproject)
    assert OFFICIAL_PACKAGE not in project["dependencies"]
    assert OFFICIAL_PACKAGE in project["optional-dependencies"]["live"]


def test_quarantined_legacy_bot_uses_fail_closed_repository_adapter() -> None:
    source = (ROOT / "bot_v3_legacy.py").read_text(encoding="utf-8")

    assert "UnsupportedTradingClient" in source
    assert ARCHIVED_IMPORT not in source
