"""Foreground runtime shutdown primitives shared by Hermes operational processes."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from types import FrameType
from typing import cast

SignalHandler = signal.Handlers | Callable[[int, FrameType | None], None]


class ShutdownController:
    """Record SIGINT/SIGTERM and let the current bounded operation finish cleanly."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._signal_number: int | None = None

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    @property
    def signal_number(self) -> int | None:
        return self._signal_number

    @property
    def exit_code(self) -> int:
        return 0 if self._signal_number is None else 128 + self._signal_number

    def request(self, signum: int) -> None:
        if self._signal_number is None:
            self._signal_number = signum
        self._event.set()

    def wait(self, timeout_seconds: float) -> bool:
        """Wait until shutdown or timeout; return True when shutdown was requested."""
        return self._event.wait(timeout_seconds)

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        self.request(signum)

    @contextmanager
    def installed(self) -> Generator[ShutdownController, None, None]:
        """Install process-local handlers temporarily and restore the previous handlers."""
        watched = (signal.SIGINT, signal.SIGTERM)
        previous: dict[signal.Signals, SignalHandler] = {}
        for signum in watched:
            previous[signum] = cast(SignalHandler, signal.getsignal(signum))
            signal.signal(signum, self._handle_signal)
        try:
            yield self
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
