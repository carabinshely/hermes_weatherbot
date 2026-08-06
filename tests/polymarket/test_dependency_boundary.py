from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARCHIVED_IMPORT = "py" + "_clob_client"
ARCHIVED_PACKAGE = "py" + "-clob-client"


def test_python_sources_do_not_import_archived_client() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path == Path(__file__).resolve():
            continue
        if any(part in {".venv", "pip_tmp"} for part in path.parts):
            continue
        if ARCHIVED_IMPORT in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_runtime_dependencies_do_not_include_archived_client() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert ARCHIVED_PACKAGE not in pyproject
    assert '"polymarket-client==0.1.0b21"' in pyproject


def test_legacy_bot_uses_fail_closed_repository_adapter() -> None:
    source = (ROOT / "bot_v3.py").read_text(encoding="utf-8")

    assert "UnsupportedTradingClient" in source
    assert ARCHIVED_IMPORT not in source
