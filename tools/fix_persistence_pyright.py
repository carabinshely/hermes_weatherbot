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
    "weatherbot/persistence/codec.py",
    '''        event_id, occurred_at = _common(data, required={"intent_id", "reason"})
        arguments = {
            "event_id": event_id,
            "occurred_at": occurred_at,
            "intent_id": OrderIntentId(_text(data["intent_id"], label="intent_id")),
            "reason": _text(data["reason"], label="reason"),
        }
        if event_type == "order_rejected":
            return OrderRejected(**arguments)
        if event_type == "order_cancelled":
            return OrderCancelled(**arguments)
        return OrderOutcomeUnknown(**arguments)
''',
    '''        event_id, occurred_at = _common(data, required={"intent_id", "reason"})
        intent_id = OrderIntentId(_text(data["intent_id"], label="intent_id"))
        reason = _text(data["reason"], label="reason")
        if event_type == "order_rejected":
            return OrderRejected(
                event_id=event_id,
                occurred_at=occurred_at,
                intent_id=intent_id,
                reason=reason,
            )
        if event_type == "order_cancelled":
            return OrderCancelled(
                event_id=event_id,
                occurred_at=occurred_at,
                intent_id=intent_id,
                reason=reason,
            )
        return OrderOutcomeUnknown(
            event_id=event_id,
            occurred_at=occurred_at,
            intent_id=intent_id,
            reason=reason,
        )
''',
)

replace_once(
    "weatherbot/persistence/store.py",
    "from collections.abc import Callable, Iterable, Iterator, Mapping\n",
    "from collections.abc import Callable, Generator, Iterable, Mapping\n",
)
replace_once(
    "weatherbot/persistence/store.py",
    "from pathlib import Path\nfrom typing import Self, cast\n",
    "from pathlib import Path\nfrom types import TracebackType\nfrom typing import Self, cast\n",
)
replace_once(
    "weatherbot/persistence/store.py",
    '''    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
''',
    '''    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
''',
)
replace_once(
    "weatherbot/persistence/store.py",
    "    def _transaction(self) -> Iterator[None]:\n",
    "    def _transaction(self) -> Generator[None, None, None]:\n",
)

replace_once(
    "tests/persistence/test_store_atomicity.py",
    '''    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda owner: _commit_same_intent(database, owner),
                ("worker-a", "worker-b"),
            )
        )
''',
    '''    def commit(owner: str) -> tuple[bool, int]:
        return _commit_same_intent(database, owner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit, ("worker-a", "worker-b")))
''',
)
