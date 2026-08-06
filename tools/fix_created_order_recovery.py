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
    "weatherbot/persistence/store.py",
    '''            for order in sorted(
                state.orders.values(),
                key=lambda item: str(item.intent.intent_id),
            ):
                if order.state is OrderState.CREATED:
                    action = RecoveryAction.RESUME_SUBMISSION
                elif order.state in {
                    OrderState.SUBMITTED,
                    OrderState.ACKNOWLEDGED,
                    OrderState.PARTIALLY_FILLED,
                    OrderState.UNKNOWN,
                }:
                    action = RecoveryAction.RECONCILE_BACKEND
                else:
                    continue
                adapter_row = self._adapter_row_locked(str(order.intent.intent_id))
                adapter = self._adapter_from_row(adapter_row) if adapter_row is not None else None
''',
    '''            for order in sorted(
                state.orders.values(),
                key=lambda item: str(item.intent.intent_id),
            ):
                adapter_row = self._adapter_row_locked(str(order.intent.intent_id))
                adapter = self._adapter_from_row(adapter_row) if adapter_row is not None else None
                if order.state is OrderState.CREATED:
                    action = (
                        RecoveryAction.RECONCILE_BACKEND
                        if adapter is not None
                        else RecoveryAction.RESUME_SUBMISSION
                    )
                elif order.state in {
                    OrderState.SUBMITTED,
                    OrderState.ACKNOWLEDGED,
                    OrderState.PARTIALLY_FILLED,
                    OrderState.UNKNOWN,
                }:
                    action = RecoveryAction.RECONCILE_BACKEND
                else:
                    continue
''',
)

replace_once(
    "docs/persistence.md",
    '''Backend reconciliation remains adapter-neutral:

```python
recovery = store.reconcile_startup(resolve_adapter)
```
''',
    '''Before calling an adapter's `submit` method, durably record its backend assignment:

```python
store.set_adapter_metadata(
    intent_id,
    backend_name="paper",
    payload={"submission_key": stable_submission_key},
)
```

This write is the submission-start marker. If the process dies after the backend accepts an order
but before `OrderSubmitted` is appended, replay still shows `created`; the marker makes startup
reconcile the backend instead of submitting the order again. A created order without the marker is
safe to resume because no backend side effect may have started yet.

Backend reconciliation remains adapter-neutral:

```python
recovery = store.reconcile_startup(resolve_adapter)
```
''',
)
