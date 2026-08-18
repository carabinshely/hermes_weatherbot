from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.pip.test_contract import REPOSITORY_ROOT, _signal
from weatherbot.pip.core import PipExportError, freeze_signal_envelope, load_release
from weatherbot.pip.outbox import PipOutbox
from weatherbot.pip.runtime import (
    MAX_RESULT_BYTES,
    PipExporterConfig,
    _parse_bound_result,
    load_exporter_config,
)


def _claimed(tmp_path: Path):
    signal = _signal()
    frozen = freeze_signal_envelope(
        signal,
        key_id="producer-key-test",
        private_key=Ed25519PrivateKey.from_private_bytes(b"\x01" * 32),
        release=load_release(REPOSITORY_ROOT, signal.strategy_version),
    )
    outbox = PipOutbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(frozen, now=frozen.generated_at)
    item = outbox.claim_due(owner_id="worker", now=frozen.generated_at)
    assert item is not None
    return outbox, item


def _binding(item):
    return {
        "producer_id": item.producer_id,
        "event_id": item.event_id,
        "event_sha256": item.event_sha256,
    }


def test_acceptance_requires_exact_event_binding_and_receipt(tmp_path: Path) -> None:
    outbox, item = _claimed(tmp_path)
    try:
        payload = {
            "contract": "pip.event-delivery-result",
            "protocol_version": "1",
            "disposition": "accepted",
            "event": _binding(item),
            "receipt_id": "receipt-1",
        }
        result = _parse_bound_result(json.dumps(payload).encode(), item)
        assert result.disposition == "accepted"
        assert result.receipt_id == "receipt-1"

        payload["event"] = {**_binding(item), "event_sha256": "0" * 64}
        with pytest.raises(PipExportError, match="does not bind"):
            _parse_bound_result(json.dumps(payload).encode(), item)
    finally:
        outbox.close()


def test_retry_and_rejection_are_closed_results(tmp_path: Path) -> None:
    outbox, item = _claimed(tmp_path)
    try:
        retry = {
            "contract": "pip.event-delivery-result",
            "protocol_version": "1",
            "disposition": "retry",
            "event": _binding(item),
            "reason_code": "ingestion.temporary",
            "retry_after_ms": 5000,
        }
        result = _parse_bound_result(json.dumps(retry).encode(), item)
        assert result.disposition == "retry"
        assert result.retry_after_ms == 5000

        rejected = {
            "contract": "pip.event-delivery-result",
            "protocol_version": "1",
            "disposition": "rejected",
            "event": _binding(item),
            "category": "authentication",
            "reason_code": "authentication.revoked_key",
        }
        result = _parse_bound_result(json.dumps(rejected).encode(), item)
        assert result.disposition == "rejected"
        assert result.result_class == "rejected:authentication"

        rejected["unexpected"] = "command"
        with pytest.raises(PipExportError, match="invalid fields"):
            _parse_bound_result(json.dumps(rejected).encode(), item)
    finally:
        outbox.close()


def test_unknown_or_malformed_results_never_acknowledge(tmp_path: Path) -> None:
    outbox, item = _claimed(tmp_path)
    try:
        for body in (
            b"{}",
            b"not-json",
            json.dumps(
                {
                    "contract": "pip.event-delivery-result",
                    "protocol_version": "1",
                    "disposition": "accepted",
                    "event": _binding(item),
                }
            ).encode(),
        ):
            with pytest.raises(PipExportError):
                _parse_bound_result(body, item)
        assert outbox.summary().in_flight == 1
        assert outbox.summary().acknowledged == 0
    finally:
        outbox.close()


def test_exporter_is_disabled_by_default_and_requires_https_for_remote_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "HERMES_PIP_EXPORT_ENABLED",
        "HERMES_PIP_ENDPOINT",
        "HERMES_PIP_OUTBOX_PATH",
        "HERMES_PIP_SIGNING_KEY_PATH",
        "HERMES_PIP_KEY_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    config = load_exporter_config(tmp_path)
    assert not config.enabled

    monkeypatch.setenv("HERMES_PIP_EXPORT_ENABLED", "true")
    monkeypatch.setenv("HERMES_PIP_ENDPOINT", "http://example.com/v1/events")
    monkeypatch.setenv("HERMES_PIP_SIGNING_KEY_PATH", "secret.key")
    monkeypatch.setenv("HERMES_PIP_KEY_ID", "key-1")
    with pytest.raises(PipExportError, match="HTTPS"):
        load_exporter_config(tmp_path)


def test_retry_claim_can_be_completed_before_lease_expires(tmp_path: Path) -> None:
    outbox, item = _claimed(tmp_path)
    try:
        now = item.generated_at + timedelta(seconds=1)
        assert outbox.retry(
            item,
            next_attempt_at=now + timedelta(seconds=10),
            result_class="network:timeout",
            http_status=None,
            now=now,
        )
        assert outbox.summary().retry_wait == 1
    finally:
        outbox.close()


def test_response_limit_is_protocol_constant() -> None:
    assert MAX_RESULT_BYTES == 65_536


def test_exporter_config_does_not_include_strategy_inputs(tmp_path: Path) -> None:
    config = PipExporterConfig(
        enabled=False,
        endpoint="",
        outbox_path=tmp_path / "outbox.sqlite3",
        signing_key_path=None,
        key_id=None,
    )
    assert not hasattr(config, "strategy_id")
    assert not hasattr(config, "policy_fingerprint")
