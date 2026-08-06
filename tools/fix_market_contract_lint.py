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
    "weatherbot/markets/orderbook.py",
    '            raise OrderBookError(f"{label} must include a timezone")\n',
    '            raise OrderBookError(f"{label} must include a timezone") from None\n',
)
replace_once(
    "weatherbot/markets/temperature.py",
    "import math\nimport re\n",
    "import math\nimport re\nfrom itertools import pairwise\n",
)
replace_once(
    "weatherbot/markets/temperature.py",
    '    rf"between\\s+{_NUMBER}\\s*(?:-|–|—|to)\\s*{_NUMBER}\\s*°?\\s*([FC])",\n',
    '    rf"between\\s+{_NUMBER}\\s*(?:-|\\u2013|\\u2014|to)\\s*{_NUMBER}\\s*°?\\s*([FC])",\n',
)
replace_once(
    "weatherbot/markets/temperature.py",
    '''        if self.upper_inclusive is not None and reported > self.upper_inclusive:
            return False
        return True
''',
    '''        return self.upper_inclusive is None or reported <= self.upper_inclusive
''',
)
replace_once(
    "weatherbot/markets/temperature.py",
    "        for previous, current in zip(ordered, ordered[1:], strict=False):\n",
    "        for previous, current in pairwise(ordered):\n",
)
