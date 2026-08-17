from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dependency_profile_documentation_matches_product_boundary() -> None:
    documentation = (ROOT / "docs" / "dependency-profiles.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "uv sync --locked --no-dev" in documentation
    assert "uv sync --locked --no-dev --extra live" in documentation
    assert "quarantined" in documentation.lower()
    assert "uv sync --locked --no-dev" in readme
    assert "uv sync --locked --no-dev --extra live" not in readme
    assert "pip install -r requirements.txt" not in readme
    assert "nicolastinkl/hermes_weatherbot" not in readme
