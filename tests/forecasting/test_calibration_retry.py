from __future__ import annotations

import urllib.error
from email.message import Message

import pytest

from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_retry import (
    is_retryable_transport_error,
    run_with_retries,
)


def _calibration_error(cause: BaseException) -> CalibrationError:
    try:
        raise CalibrationError("historical data request failed for https://example.test") from cause
    except CalibrationError as exc:
        return exc


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test",
        code,
        message,
        Message(),
        None,
    )


def test_retryable_transport_error_accepts_timeout_and_selected_http_statuses() -> None:
    timeout = _calibration_error(urllib.error.URLError(TimeoutError("timed out")))
    unavailable = _calibration_error(_http_error(503, "unavailable"))
    missing = _calibration_error(_http_error(404, "missing"))

    assert is_retryable_transport_error(timeout)
    assert is_retryable_transport_error(unavailable)
    assert not is_retryable_transport_error(missing)


def test_retryable_transport_error_rejects_unrelated_oserror_chain() -> None:
    try:
        raise CalibrationError("invalid cache metadata") from OSError("disk read failed")
    except CalibrationError as exc:
        error = exc

    assert not is_retryable_transport_error(error)


def test_run_with_retries_resumes_after_transient_failures_and_logs_attempts() -> None:
    attempts = 0
    sleeps: list[float] = []
    logs: list[str] = []

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
        log=logs.append,
    )

    assert result == 17
    assert attempts == 3
    assert sleeps == [2.0, 4.0]
    assert logs[0] == "calibration sweep attempt 1/4"
    assert "retrying in 2s" in logs[1]
    assert logs[2] == "calibration sweep attempt 2/4"
    assert "retrying in 4s" in logs[3]
    assert logs[4] == "calibration sweep attempt 3/4"


def test_run_with_retries_does_not_retry_data_validation_failure() -> None:
    attempts = 0
    sleeps: list[float] = []
    logs: list[str] = []

    def operation() -> int:
        nonlocal attempts
        attempts += 1
        raise CalibrationError("cached payload hash does not match payload bytes")

    with pytest.raises(CalibrationError, match="payload hash"):
        run_with_retries(operation, sleep=sleeps.append, log=logs.append)

    assert attempts == 1
    assert sleeps == []
    assert logs[0] == "calibration sweep attempt 1/4"
    assert "non-retryable" in logs[1]


def test_run_with_retries_stops_at_attempt_limit_and_logs_exhaustion() -> None:
    attempts = 0
    sleeps: list[float] = []
    logs: list[str] = []

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
            log=logs.append,
        )

    assert attempts == 3
    assert sleeps == [1.5, 3.0]
    assert logs[-2] == "calibration sweep attempt 3/3"
    assert "exhausted 3 transport attempts" in logs[-1]


def test_run_with_retries_rejects_invalid_retry_policy() -> None:
    def operation() -> int:
        return 0

    with pytest.raises(ValueError, match="positive integer"):
        run_with_retries(operation, max_attempts=0)
    with pytest.raises(ValueError, match="non-negative"):
        run_with_retries(operation, backoff_seconds=-0.1)
