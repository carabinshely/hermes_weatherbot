"""Bounded retry wrapper for long-running historical calibration collection.

The immutable HTTP cache already makes the sweep resumable: every successful response is
frozen before parsing. This wrapper retries only transport-level failures, so a transient
provider timeout can resume from the frozen cache without weakening any data-validation
failure into a retryable condition.
"""

from __future__ import annotations

import argparse
import time
import urllib.error
from collections.abc import Callable, Sequence

from weatherbot.forecasting import calibration_sweep
from weatherbot.forecasting.calibration import CalibrationError

_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_BACKOFF_SECONDS = 2.0
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_retryable_transport_error(exc: CalibrationError) -> bool:
    """Return whether ``exc`` came from a retryable HTTP/transport failure."""

    cause = exc.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        return cause.code in _RETRYABLE_HTTP_STATUS
    return isinstance(cause, OSError)


def run_with_retries(
    operation: Callable[[], int],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run ``operation`` with deterministic exponential backoff for transport failures."""

    if isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must be non-negative")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except CalibrationError as exc:
            if not is_retryable_transport_error(exc) or attempt == max_attempts:
                raise
            sleep(backoff_seconds * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the issue #12 calibration sweep with bounded transport retries"
    )
    parser.add_argument("--max-attempts", type=int, default=_DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--retry-backoff-seconds", type=float, default=_DEFAULT_BACKOFF_SECONDS)
    parser.add_argument(
        "sweep_args",
        nargs=argparse.REMAINDER,
        help="arguments forwarded to calibration_sweep; separate them with --",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sweep_args = list(args.sweep_args)
    if sweep_args and sweep_args[0] == "--":
        sweep_args = sweep_args[1:]
    if not sweep_args:
        raise SystemExit("calibration sweep arguments are required after --")
    return run_with_retries(
        lambda: calibration_sweep.main(sweep_args),
        max_attempts=args.max_attempts,
        backoff_seconds=args.retry_backoff_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
