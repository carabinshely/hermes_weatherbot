from __future__ import annotations

from pathlib import Path


def test_summary_keeps_authoritative_resolver_out_of_scope() -> None:
    summary = Path("docs/resolution-foundation-summary.md").read_text(encoding="utf-8")
    assert "Issue #13 remains open" in summary
    assert "not implemented or claimed complete" in summary
