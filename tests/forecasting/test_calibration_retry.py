from __future__ import annotations

import urllib.error
from collections.abc import Callable

import pytest

from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_retry import (
    is_retryable_transport_error,
    run_with_retries,
)


def _calibration_error(cause: BaseException) -> CalibrationError:
    try:
        raise CalibrationError("historical data request failed") from cause
    except CalibrationError as exc:
        return exc


def test_retryable_transport_error_accepts_timeout_and_selected_http_statuses() -> None:
    timeout = _calibration_error(urllib.error.URLError(TimeoutError("timed out")))
    unavailable = _calibration_error(
        urllib.error.HTTPError("https://example.test", 503, "unavailable", {}, None)
    )
    missing = _calibration_error(
        urllib.error.HTTPError("https://example.test", 404, "missing", {}, None)
    )

    assert is_retryable_transport_error(timeout)
    assert is_retryable_transport_error(unavailable)
    assert not is_retryable_transport_error(missing)


def test_run_with_retries_resumes_after_transient_failures() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> int:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _calibration_error(urllib.error.URLError(TimeoutError("timed out")))
        return 17

    result = run_with_retries(
        operation,
        max_attempts=4,
        backoff_seconds=2.0,
        sleep=sleeps.append,
    )

    assert result == 17
    assert attempts == 3
    assert sleeps == [2.0, 4.0]


def test_run_with_retries_does_not_retry_data_validation_failure() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> int:
        nonlocal attempts
        attempts += 1
        raise CalibrationError("cached payload hash does not match payload bytes")

    with pytest.raises(CalibrationError, match="payload hash"):
        run_with_retries(operation, sleep=sleeps.append)

    assert attempts == 1
    assert sleeps == []


def test_run_with_retries_stops_at_attempt_limit() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> int:
        nonlocal attempts
        attempts += 1
        raise _calibration_error(urllib.error.URLError(TimeoutError("timed out")))

    with pytest.raises(CalibrationError, match="historical data request failed"):
        run_with_retries(
            operation,
            max_attempts=3,
            backoff_seconds=1.5,
            sleep=sleeps.append,
        )

    assert attempts == 3
    assert sleeps == [1.5, 3.0]


def test_run_with_retries_rejects_invalid_retry_policy() -> None:
    operation: Callable[[], int] = lambda: 0

    with pytest.raises(ValueError, match="positive integer"):
        run_with_retries(operation, max_attempts=0)
    with pytest.raises(ValueError, match="non-negative"):
        run_with_retries(operation, backoff_seconds=-0.1)
