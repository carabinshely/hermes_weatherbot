from __future__ import annotations

from pathlib import Path

path = Path("weatherbot/domain/reducers.py")
content = path.read_text(encoding="utf-8")
old = '''    elif evidence.status is ObservationEvidenceStatus.REVISED:
        if not terminal_history:
            raise DuplicateEventConflict(
                "weather observation revision requires an existing final root"
            )
        latest = terminal_history[-1]
'''
new = '''    elif evidence.status is ObservationEvidenceStatus.REVISED:
        superseded = next(
            (
                prior
                for prior in existing
                if prior.payload_hash == evidence.supersedes_payload_hash
            ),
            None,
        )
        if superseded is None:
            raise DuplicateEventConflict(
                "weather observation revision supersedes an unknown payload"
            )
        if not terminal_history:
            raise DuplicateEventConflict(
                "weather observation revision requires an existing final root"
            )
        latest = terminal_history[-1]
'''
if content.count(old) != 1:
    raise SystemExit(f"expected one revision marker, found {content.count(old)}")
path.write_text(content.replace(old, new, 1), encoding="utf-8")
