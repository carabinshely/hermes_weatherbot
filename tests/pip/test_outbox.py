from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.pip.test_contract import REPOSITORY_ROOT, _signal
from weatherbot.pip.core import PipExportError, freeze_signal_envelope, load_release
from weatherbot.pip.outbox import PipOutbox


def _frozen():
    signal = _signal()
    return freeze_signal_envelope(
        signal,
        key_id="producer-key-test",
        private_key=Ed25519PrivateKey.from_private_bytes(b"\x01" * 32),
        release=load_release(REPOSITORY_ROOT, signal.strategy_version),
    )


def test_enqueue_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    frozen = _frozen()
    with PipOutbox(tmp_path / "outbox.sqlite3") as outbox:
        first = outbox.enqueue(frozen, now=frozen.generated_at)
        second = outbox.enqueue(frozen, now=frozen.generated_at)
        assert first.outbox_id == second.outbox_id

        conflict = replace(frozen, event_sha256="f" * 64)
        with pytest.raises(PipExportError, match="identity conflict"):
            outbox.enqueue(conflict, now=frozen.generated_at)


def test_expired_automatic_claim_recovers_as_ambiguous_retry(tmp_path: Path) -> None:
    frozen = _frozen()
    with PipOutbox(tmp_path / "outbox.sqlite3") as outbox:
        outbox.enqueue(frozen, now=frozen.generated_at)
        claimed = outbox.claim_due(
            owner_id="worker-a",
            now=frozen.generated_at,
            lease_seconds=1,
        )
        assert claimed is not None
        assert claimed.state == "in_flight"
        outbox.recover(now=frozen.generated_at + timedelta(seconds=2))
        summary = outbox.summary()
        assert summary.retry_wait == 1
        assert summary.in_flight == 0


def test_stale_claimant_cannot_ack_after_lease_expiry(tmp_path: Path) -> None:
    frozen = _frozen()
    with PipOutbox(tmp_path / "outbox.sqlite3") as outbox:
        outbox.enqueue(frozen, now=frozen.generated_at)
        claimed = outbox.claim_due(
            owner_id="worker-a",
            now=frozen.generated_at,
            lease_seconds=1,
        )
        assert claimed is not None
        assert not outbox.acknowledge(
            claimed,
            receipt_id="receipt-1",
            result_class="accepted",
            http_status=200,
            now=frozen.generated_at + timedelta(seconds=2),
        )


def test_delivery_horizon_retains_item_as_dead_letter(tmp_path: Path) -> None:
    frozen = _frozen()
    with PipOutbox(tmp_path / "outbox.sqlite3") as outbox:
        outbox.enqueue(frozen, now=frozen.generated_at)
        outbox.recover(now=frozen.generated_at + timedelta(days=7, seconds=1))
        summary = outbox.summary()
        assert summary.dead_letter == 1
        assert summary.pending == 0


def test_operator_can_dead_letter_pending_item_without_mutating_frozen_bytes(
    tmp_path: Path,
) -> None:
    frozen = _frozen()
    with PipOutbox(tmp_path / "outbox.sqlite3") as outbox:
        original = outbox.enqueue(frozen, now=frozen.generated_at)
        assert outbox.operator_dead_letter(
            event_id=frozen.event_id,
            operator_id="operator-1",
            reason="maintenance",
            now=frozen.generated_at + timedelta(seconds=1),
        )
        assert outbox.summary().dead_letter == 1
        claimed = outbox.claim_dead_letter_once(
            event_id=frozen.event_id,
            owner_id="worker-b",
            operator_id="operator-1",
            reason="approved retry",
            now=frozen.generated_at + timedelta(seconds=2),
        )
        assert claimed.envelope_bytes == original.envelope_bytes
        assert claimed.event_sha256 == original.event_sha256
        assert claimed.claim_mode == "operator_one_shot"


def test_unacknowledged_operator_one_shot_returns_to_dead_letter(tmp_path: Path) -> None:
    frozen = _frozen()
    with PipOutbox(tmp_path / "outbox.sqlite3") as outbox:
        outbox.enqueue(frozen, now=frozen.generated_at)
        assert outbox.operator_dead_letter(
            event_id=frozen.event_id,
            operator_id="operator-1",
            reason="manual hold",
            now=frozen.generated_at,
        )
        claimed = outbox.claim_dead_letter_once(
            event_id=frozen.event_id,
            owner_id="worker-b",
            operator_id="operator-1",
            reason="one shot",
            now=frozen.generated_at + timedelta(seconds=1),
        )
        assert outbox.retry(
            claimed,
            next_attempt_at=frozen.generated_at + timedelta(seconds=10),
            result_class="network:Timeout",
            http_status=None,
            now=frozen.generated_at + timedelta(seconds=2),
        )
        summary = outbox.summary()
        assert summary.dead_letter == 1
        assert summary.retry_wait == 0
