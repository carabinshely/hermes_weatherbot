from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path

import requests
import urllib3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.pip.test_contract import REPOSITORY_ROOT, make_signal
from weatherbot.pip import deliver_dead_letter_once, deliver_once, retry_delay_bounds
from weatherbot.pip.core import FrozenEnvelope, freeze_signal_envelope, load_release
from weatherbot.pip.outbox import PipOutbox
from weatherbot.pip.runtime import MAX_RESULT_BYTES, PipExporterConfig


class FakeAdapter(requests.adapters.BaseAdapter):
    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"",
        location: str | None = None,
        error: requests.RequestException | None = None,
    ) -> None:
        super().__init__()
        self.status = status
        self.body = body
        self.location = location
        self.error = error
        self.send_count = 0
        self.last_body: bytes | str | None = None

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: object = None,
        proxies: dict[str, str] | None = None,
    ) -> requests.Response:
        del stream, timeout, verify, cert, proxies
        self.send_count += 1
        body = request.body
        self.last_body = body if isinstance(body, (bytes, str)) else None
        if self.error is not None:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        response.request = request
        response.url = request.url or ""
        if self.location is not None:
            response.headers["Location"] = self.location
        response.raw = urllib3.response.HTTPResponse(
            body=io.BytesIO(self.body),
            preload_content=False,
        )
        return response

    def close(self) -> None:
        return None


def _frozen() -> FrozenEnvelope:
    signal = make_signal()
    return freeze_signal_envelope(
        signal,
        key_id="producer-key-test",
        private_key=Ed25519PrivateKey.from_private_bytes(b"\x01" * 32),
        release=load_release(REPOSITORY_ROOT, signal.strategy_version),
    )


def _config(tmp_path: Path) -> PipExporterConfig:
    return PipExporterConfig(
        enabled=True,
        endpoint="http://localhost/v1/events",
        outbox_path=tmp_path / "outbox.sqlite3",
        signing_key_path=None,
        key_id=None,
    )


def _session(adapter: FakeAdapter) -> requests.Session:
    session = requests.Session()
    session.mount("http://", adapter)
    return session


def _result_body(
    frozen: FrozenEnvelope,
    disposition: str,
    *,
    receipt_id: str | None = None,
    category: str | None = None,
    reason_code: str | None = None,
) -> bytes:
    result: dict[str, object] = {
        "contract": "pip.event-delivery-result",
        "protocol_version": "1",
        "disposition": disposition,
        "event": {
            "producer_id": frozen.producer_id,
            "event_id": frozen.event_id,
            "event_sha256": frozen.event_sha256,
        },
    }
    if receipt_id is not None:
        result["receipt_id"] = receipt_id
    if category is not None:
        result["category"] = category
    if reason_code is not None:
        result["reason_code"] = reason_code
    return json.dumps(result, separators=(",", ":")).encode("utf-8")


def _enqueue(tmp_path: Path) -> tuple[PipExporterConfig, FrozenEnvelope]:
    config = _config(tmp_path)
    frozen = _frozen()
    with PipOutbox(config.outbox_path) as outbox:
        outbox.enqueue(frozen, now=frozen.generated_at)
    return config, frozen


def test_bound_acceptance_acks_and_sends_exact_frozen_bytes(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(
        body=_result_body(frozen, "accepted", receipt_id="receipt-1"),
    )
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().acknowledged == 1
    assert adapter.last_body == frozen.envelope_bytes


def test_already_accepted_original_receipt_acks_after_ambiguous_prior_attempt(
    tmp_path: Path,
) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(
        body=_result_body(frozen, "already_accepted", receipt_id="receipt-1"),
    )
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().acknowledged == 1


def test_bare_202_and_malformed_success_remain_retryable(tmp_path: Path) -> None:
    for index, body in enumerate((b"{}", b"not-json")):
        case_path = tmp_path / str(index)
        config, frozen = _enqueue(case_path)
        adapter = FakeAdapter(status=202, body=body)
        with _session(adapter) as session:
            assert deliver_once(
                config=config,
                session=session,
                now=frozen.generated_at,
                completion_now=frozen.generated_at + timedelta(seconds=1),
            )
        with PipOutbox(config.outbox_path) as outbox:
            assert outbox.summary().retry_wait == 1
            assert outbox.summary().acknowledged == 0


def test_bound_rejection_dead_letters(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(
        status=400,
        body=_result_body(
            frozen,
            "rejected",
            category="authentication",
            reason_code="authentication.revoked_key",
        ),
    )
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().dead_letter == 1


def test_network_failure_is_retryable(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(error=requests.Timeout("timeout"))
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().retry_wait == 1


def test_redirect_is_not_followed_and_remains_retryable(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(status=302, location="https://example.invalid/v1/events")
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    assert adapter.send_count == 1
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().retry_wait == 1


def test_oversized_decoded_result_is_ambiguous_retry(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(body=b"x" * (MAX_RESULT_BYTES + 1))
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().retry_wait == 1


def test_completion_after_lease_expiry_cannot_write_receipt(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    adapter = FakeAdapter(
        body=_result_body(frozen, "accepted", receipt_id="too-late"),
    )
    late = frozen.generated_at + timedelta(seconds=61)
    with _session(adapter) as session:
        assert deliver_once(
            config=config,
            session=session,
            now=frozen.generated_at,
            completion_now=late,
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().acknowledged == 0
        assert outbox.summary().in_flight == 1
        outbox.recover(now=late)
        assert outbox.summary().retry_wait == 1


def test_operator_one_shot_non_ack_returns_to_dead_letter(tmp_path: Path) -> None:
    config, frozen = _enqueue(tmp_path)
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.operator_dead_letter(
            event_id=frozen.event_id,
            operator_id="operator-1",
            reason="manual hold",
            now=frozen.generated_at,
        )
    adapter = FakeAdapter(error=requests.Timeout("timeout"))
    with _session(adapter) as session:
        assert deliver_dead_letter_once(
            config=config,
            event_id=frozen.event_id,
            operator_id="operator-1",
            reason="one shot",
            session=session,
            now=frozen.generated_at,
            completion_now=frozen.generated_at + timedelta(seconds=1),
        )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().dead_letter == 1
        assert outbox.summary().retry_wait == 0


def test_retry_policy_matches_frozen_pip_vectors() -> None:
    assert retry_delay_bounds(1) == (0.5, 1.0)
    assert retry_delay_bounds(2) == (1.0, 2.0)
    assert retry_delay_bounds(7) == (30.0, 60.0)
    assert retry_delay_bounds(1_000_000) == (30.0, 60.0)
