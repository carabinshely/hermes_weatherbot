from __future__ import annotations

import urllib.error
from email.message import Message

import pytest

from weatherbot.forecasting.calibration_retry import (
    is_retryable_request_error,
    run_request_with_retries,
)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test",
        code,
        "test error",
        Message(),
        None,
    )


def test_request_retry_retries_same_operation_in_place() -> None:
    attempts = 0
    sleeps: list[float] = []
    logs: list[str] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return "captured"

    result = run_request_with_retries(
        operation,
        max_attempts=4,
        backoff_seconds=3.0,
        sleep=sleeps.append,
        log=logs.append,
    )

    assert result == "captured"
    assert attempts == 3
    assert sleeps == [3.0, 6.0]
    assert "attempt 1/4" in logs[0]
    assert "retrying in 3s" in logs[0]
    assert "attempt 2/4" in logs[1]
    assert "retrying in 6s" in logs[1]


def test_request_retry_rejects_nonretryable_http_status() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise _http_error(404)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        run_request_with_retries(operation, sleep=sleeps.append, log=lambda _: None)

    assert exc_info.value.code == 404
    assert attempts == 1
    assert sleeps == []
    assert not is_retryable_request_error(exc_info.value)


def test_request_retry_exhaustion_is_bounded_and_logged() -> None:
    attempts = 0
    sleeps: list[float] = []
    logs: list[str] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise urllib.error.URLError(TimeoutError("timed out"))

    with pytest.raises(urllib.error.URLError):
        run_request_with_retries(
            operation,
            max_attempts=3,
            backoff_seconds=1.0,
            sleep=sleeps.append,
            log=logs.append,
        )

    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    assert "exhausted 3 transport attempts" in logs[-1]
