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
    "tests/resolution/test_worker.py",
    '''        assert len(eligible_resolution_evidence(state)) == (
            0 if status is ResolutionPollStatus.VOID else 1
        )
''',
    '''        assert eligible_resolution_evidence(state) == ()
        assert not state.weather_observations
''',
)
replace_once(
    "tests/resolution/test_observations.py",
    "        source_timestamp=NOW - timedelta(hours=1),\n",
    "        source_timestamp=NOW - timedelta(hours=2),\n",
)
