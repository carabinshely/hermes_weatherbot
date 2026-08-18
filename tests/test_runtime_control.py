from __future__ import annotations

import signal

from weatherbot.runtime_control import ShutdownController


def test_shutdown_controller_keeps_first_signal_and_interrupts_wait() -> None:
    controller = ShutdownController()

    assert not controller.requested
    assert controller.exit_code == 0

    controller.request(signal.SIGTERM)
    controller.request(signal.SIGINT)

    assert controller.requested
    assert controller.signal_number == signal.SIGTERM
    assert controller.exit_code == 143
    assert controller.wait(10.0)


def test_installed_handlers_are_restored() -> None:
    controller = ShutdownController()
    before_int = signal.getsignal(signal.SIGINT)
    before_term = signal.getsignal(signal.SIGTERM)

    with controller.installed():
        assert signal.getsignal(signal.SIGINT) != before_int
        assert signal.getsignal(signal.SIGTERM) != before_term

    assert signal.getsignal(signal.SIGINT) == before_int
    assert signal.getsignal(signal.SIGTERM) == before_term
