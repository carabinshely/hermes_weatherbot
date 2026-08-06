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
    "weatherbot/markets/orderbook.py",
    "    levels = []\n",
    "    levels: list[OrderLevel] = []\n",
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
replace_once(
    "weatherbot/markets/temperature.py",
    '''def parse_temperature_bucket(question: str) -> TemperatureBucket:
    if not isinstance(question, str) or not question.strip():
''',
    '''def parse_temperature_bucket(question: str) -> TemperatureBucket:
    if not question.strip():
''',
)
replace_once(
    "tests/markets/test_orderbook.py",
    '''@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update({"bids": []}),
        lambda data: data.update({"asks": []}),
        lambda data: data.update(
            {
                "bids": [{"price": "0.41", "size": "1"}],
                "asks": [{"price": "0.40", "size": "1"}],
            }
        ),
        lambda data: data.update(
            {
                "bids": [
                    {"price": "0.33", "size": "1"},
                    {"price": "0.34", "size": "1"},
                ]
            }
        ),
        lambda data: data.update(
            {
                "asks": [
                    {"price": "0.42", "size": "1"},
                    {"price": "0.40", "size": "1"},
                ]
            }
        ),
    ],
)
''',
    '''def _empty_bids(data: dict[str, object]) -> None:
    data["bids"] = []


def _empty_asks(data: dict[str, object]) -> None:
    data["asks"] = []


def _cross_book(data: dict[str, object]) -> None:
    data["bids"] = [{"price": "0.41", "size": "1"}]
    data["asks"] = [{"price": "0.40", "size": "1"}]


def _unsorted_bids(data: dict[str, object]) -> None:
    data["bids"] = [
        {"price": "0.33", "size": "1"},
        {"price": "0.34", "size": "1"},
    ]


def _unsorted_asks(data: dict[str, object]) -> None:
    data["asks"] = [
        {"price": "0.42", "size": "1"},
        {"price": "0.40", "size": "1"},
    ]


_BOOK_MUTATIONS: tuple[Callable[[dict[str, object]], None], ...] = (
    _empty_bids,
    _empty_asks,
    _cross_book,
    _unsorted_bids,
    _unsorted_asks,
)


@pytest.mark.parametrize("mutation", _BOOK_MUTATIONS)
''',
)
