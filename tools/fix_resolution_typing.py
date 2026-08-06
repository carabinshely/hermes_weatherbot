from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    content = file.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one marker, found {count}")
    file.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "weatherbot/resolution/worker.py",
    "from collections.abc import Callable, Protocol\n",
    "from collections.abc import Callable\nfrom typing import Protocol\n",
)
replace_once(
    "weatherbot/resolution/gamma.py",
    "                if market.end_at is not None and checked > market.end_at + self.delay_grace:\n",
    "                if market.end_at is not None and checked >= market.end_at + self.delay_grace:\n",
)
replace_once(
    "weatherbot/domain/reducers.py",
    '''def _apply_resolution_evidence(
    state: LedgerState,
    event: MarketResolutionEvidenceRecorded,
) -> LedgerState:
    market_id = event.evidence.market_id
''',
    '''def _apply_resolution_evidence(
    state: LedgerState,
    event: MarketResolutionEvidenceRecorded,
) -> LedgerState:
    _require_opened(state)
    market_id = event.evidence.market_id
''',
)
replace_once(
    "weatherbot/resolution/worker.py",
    '''        return ResolutionCycleItem(
            market_id=resolution.market_id,
            status=ResolutionPollStatus.FINAL,
            reason="settled positions from an already-recorded market resolution",
''',
    '''        evidence = state.resolution_evidence.get(resolution.market_id)
        status = (
            ResolutionPollStatus.VOID
            if evidence is not None and not evidence.learning_eligible
            else ResolutionPollStatus.FINAL
        )
        return ResolutionCycleItem(
            market_id=resolution.market_id,
            status=status,
            reason="settled positions from an already-recorded market resolution",
''',
)
