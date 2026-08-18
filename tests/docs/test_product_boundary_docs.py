from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

MAINTAINED_BOUNDARY_DOCS = (
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "docs" / "paper-trading.md",
    ROOT / "docs" / "executable-edge.md",
    ROOT / "docs" / "portfolio-risk.md",
    ROOT / "docs" / "polymarket-sdk.md",
    ROOT / "docs" / "resolution-worker.md",
    ROOT / "docs" / "market-resolution-contracts.md",
    ROOT / "docs" / "persistence.md",
    ROOT / "docs" / "forecast-calibration.md",
)

FORBIDDEN_OBSOLETE_CLAIMS = (
    "true probability",
    "bot_v3.py scan --mode paper",
    "bot_v3.py run --mode paper",
    "bot_v3.py status --mode paper",
    "paper-reset --mode paper",
    "future live adapter",
    "future live order",
    "future one-way pip publication",
    "optional future #54 pip adapter",
)


def _lower_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_maintained_docs_do_not_restore_obsolete_product_claims() -> None:
    violations: list[str] = []
    for path in MAINTAINED_BOUNDARY_DOCS:
        text = _lower_text(path)
        for phrase in FORBIDDEN_OBSOLETE_CLAIMS:
            if phrase in text:
                violations.append(f"{path.relative_to(ROOT)}: {phrase!r}")

    assert not violations, "obsolete product-boundary claims found:\n" + "\n".join(violations)


def test_readme_states_current_public_paper_and_pip_boundaries() -> None:
    text = _lower_text(ROOT / "README.md")

    required = (
        "non-executing calibrated weather prediction-market signal producer",
        "signed signalenvelope v1",
        "durable pip outbox",
        "python -m weatherbot.paper evaluate",
        "hypothetical development evidence",
        "pip independently preserves, resolves, and scores",
        "accepted v3 artifact          = absent",
    )

    missing = [phrase for phrase in required if phrase not in text]
    assert not missing, "README is missing boundary authority: " + ", ".join(missing)


def test_security_distinguishes_pip_identity_from_financial_credentials() -> None:
    text = _lower_text(ROOT / "SECURITY.md")
    assert "ed25519" in text
    assert "application-identity" in text
    assert "no wallet" in text
    assert "not supported hermes product credentials" in text
