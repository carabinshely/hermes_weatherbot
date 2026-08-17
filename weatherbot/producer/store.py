"""Durable local persistence for real Hermes producer signals."""

from __future__ import annotations

import json
import os
from pathlib import Path

from weatherbot.producer.model import HermesSignal


def append_signal(path: Path, signal: HermesSignal) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        signal.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()
        os.fsync(handle.fileno())
