from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.pip.test_contract import REPOSITORY_ROOT, make_signal
from tests.quoting.helpers import NOW
from weatherbot.pip import (
    PipExporterConfig,
    PipExportError,
    PipIntentStore,
    PipOutbox,
    freeze_signal_envelope,
    load_release,
    promote_staged_signal,
    reconcile_signal_log,
    stage_signal,
)
from weatherbot.producer.model import HermesSignal


def _key_file(tmp_path: Path, byte: int) -> Path:
    path = tmp_path / f"key-{byte}.txt"
    encoded = base64.urlsafe_b64encode(bytes([byte]) * 32).decode("ascii").rstrip("=")
    path.write_text(encoded, encoding="ascii")
    path.chmod(0o600)
    return path


def _config(tmp_path: Path, *, key_byte: int, key_id: str) -> PipExporterConfig:
    return PipExporterConfig(
        enabled=True,
        endpoint="http://localhost/v1/events",
        outbox_path=tmp_path / "outbox.sqlite3",
        signing_key_path=_key_file(tmp_path, key_byte),
        key_id=key_id,
    )


def _write_signals(path: Path, *signals: HermesSignal) -> None:
    records = [
        json.dumps(signal.to_mapping(), sort_keys=True, separators=(",", ":"))
        for signal in signals
    ]
    path.write_text("\n".join(records) + "\n", encoding="utf-8")


def test_staged_bytes_survive_key_rotation_before_outbox_promotion(tmp_path: Path) -> None:
    signal = make_signal()
    old_config = _config(tmp_path, key_byte=1, key_id="old-key")
    new_config = _config(tmp_path, key_byte=2, key_id="new-key")

    assert stage_signal(
        signal,
        config=old_config,
        repository_root=REPOSITORY_ROOT,
        now=signal.generated_at_utc,
    )
    with PipIntentStore(old_config.outbox_path) as intents:
        frozen_before_crash = intents.get(signal.signal_id)
    assert frozen_before_crash is not None
    assert frozen_before_crash.key_id == "old-key"

    # Simulate restart after JSONL fsync with a successor key now configured. Promotion must use
    # the exact staged bytes and key identity, never rebuild/re-sign the old event.
    assert promote_staged_signal(
        signal.signal_id,
        config=new_config,
        now=signal.generated_at_utc + timedelta(seconds=1),
    )
    with PipOutbox(new_config.outbox_path) as outbox:
        claimed = outbox.claim_due(
            owner_id="worker",
            now=signal.generated_at_utc + timedelta(seconds=2),
        )
        assert claimed is not None
        assert claimed.key_id == "old-key"
        assert claimed.envelope_bytes == frozen_before_crash.envelope_bytes
        assert claimed.event_sha256 == frozen_before_crash.event_sha256


def test_uncommitted_staged_intent_is_never_automatically_deliverable(tmp_path: Path) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    assert stage_signal(
        signal,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=signal.generated_at_utc,
    )

    with PipIntentStore(config.outbox_path) as intents:
        assert intents.count() == 1
    with PipOutbox(config.outbox_path) as outbox:
        summary = outbox.summary()
        assert summary.pending == 0
        assert summary.in_flight == 0
        assert summary.retry_wait == 0


def test_reconciliation_discards_staging_without_a_durable_signal(tmp_path: Path) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    assert stage_signal(
        signal,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=signal.generated_at_utc,
    )
    with PipIntentStore(config.outbox_path) as intents:
        assert intents.count() == 1

    assert (
        reconcile_signal_log(
            tmp_path / "missing-signals.jsonl",
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=signal.generated_at_utc + timedelta(seconds=1),
        )
        == 0
    )
    with PipIntentStore(config.outbox_path) as intents:
        assert intents.count() == 0
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 0


def test_known_uncommitted_staging_can_be_discarded(tmp_path: Path) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    assert stage_signal(
        signal,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=signal.generated_at_utc,
    )
    with PipIntentStore(config.outbox_path) as intents:
        assert intents.count() == 1
        assert intents.discard(signal.signal_id)
        assert not intents.discard(signal.signal_id)
        assert intents.count() == 0
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 0


def test_reconciliation_promotes_existing_intent_without_current_key(tmp_path: Path) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="old-key")
    assert stage_signal(
        signal,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=signal.generated_at_utc,
    )
    assert config.signing_key_path is not None
    config.signing_key_path.unlink()

    signal_log = tmp_path / "signals.jsonl"
    _write_signals(signal_log, signal)
    assert (
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=signal.generated_at_utc + timedelta(seconds=1),
        )
        == 1
    )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 1


def test_repeated_hermes_signal_id_creates_exactly_one_pip_event_from_first_record(
    tmp_path: Path,
) -> None:
    first = make_signal(NOW)
    second = make_signal(NOW + timedelta(seconds=1))
    assert first.signal_id == second.signal_id
    assert first.generated_at_utc != second.generated_at_utc
    assert first.market_reference.quote_fingerprint != second.market_reference.quote_fingerprint

    config = _config(tmp_path, key_byte=1, key_id="key-1")
    signal_log = tmp_path / "signals.jsonl"
    _write_signals(signal_log, first, second)

    assert (
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=second.generated_at_utc + timedelta(seconds=1),
        )
        == 1
    )
    expected = freeze_signal_envelope(
        first,
        key_id="key-1",
        private_key=Ed25519PrivateKey.from_private_bytes(b"\x01" * 32),
        release=load_release(REPOSITORY_ROOT, first.strategy_version),
    )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 1
        claimed = outbox.claim_due(
            owner_id="worker",
            now=second.generated_at_utc + timedelta(seconds=2),
        )
        assert claimed is not None
        assert claimed.envelope_bytes == expected.envelope_bytes
        assert claimed.signal_id == first.signal_id

    assert (
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=second.generated_at_utc + timedelta(seconds=3),
        )
        == 0
    )


def test_orphan_staging_cannot_override_different_durable_occurrence(tmp_path: Path) -> None:
    uncommitted = make_signal(NOW)
    durable = make_signal(NOW + timedelta(seconds=1))
    assert uncommitted.signal_id == durable.signal_id

    old_config = _config(tmp_path, key_byte=1, key_id="old-key")
    assert stage_signal(
        uncommitted,
        config=old_config,
        repository_root=REPOSITORY_ROOT,
        now=uncommitted.generated_at_utc,
    )
    with PipIntentStore(old_config.outbox_path) as intents:
        orphan = intents.get(uncommitted.signal_id)
    assert orphan is not None

    new_config = _config(tmp_path, key_byte=2, key_id="new-key")
    signal_log = tmp_path / "signals.jsonl"
    _write_signals(signal_log, durable)
    assert (
        reconcile_signal_log(
            signal_log,
            config=new_config,
            repository_root=REPOSITORY_ROOT,
            now=durable.generated_at_utc + timedelta(seconds=1),
        )
        == 1
    )
    expected = freeze_signal_envelope(
        durable,
        key_id="new-key",
        private_key=Ed25519PrivateKey.from_private_bytes(b"\x02" * 32),
        release=load_release(REPOSITORY_ROOT, durable.strategy_version),
    )
    with PipOutbox(new_config.outbox_path) as outbox:
        claimed = outbox.claim_due(
            owner_id="worker",
            now=durable.generated_at_utc + timedelta(seconds=2),
        )
        assert claimed is not None
        assert claimed.key_id == "new-key"
        assert claimed.envelope_bytes == expected.envelope_bytes
        assert claimed.envelope_bytes != orphan.envelope_bytes


def test_reconciliation_retires_stale_intent_after_outbox_enqueue_crash(tmp_path: Path) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    assert stage_signal(
        signal,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=signal.generated_at_utc,
    )
    with PipIntentStore(config.outbox_path) as intents:
        staged = intents.get(signal.signal_id)
    assert staged is not None
    with PipOutbox(config.outbox_path) as outbox:
        outbox.enqueue(staged, now=signal.generated_at_utc)
    with PipIntentStore(config.outbox_path) as intents:
        assert intents.count() == 1

    signal_log = tmp_path / "signals.jsonl"
    _write_signals(signal_log, signal)
    assert (
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=signal.generated_at_utc + timedelta(seconds=1),
        )
        == 0
    )
    with PipIntentStore(config.outbox_path) as intents:
        assert intents.count() == 0
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 1


def test_later_scan_after_existing_create_is_lifecycle_idempotent(tmp_path: Path) -> None:
    first = make_signal(NOW)
    later = make_signal(NOW + timedelta(seconds=1))
    assert first.signal_id == later.signal_id
    config = _config(tmp_path, key_byte=1, key_id="key-1")

    assert stage_signal(
        first,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=first.generated_at_utc,
    )
    assert promote_staged_signal(first.signal_id, config=config, now=first.generated_at_utc)
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 1

    # A later real Hermes observation may have different processing metadata, but lifecycle v1
    # already owns signal.created for this logical signal. Local staging/promotion is a safe no-op.
    assert stage_signal(
        later,
        config=config,
        repository_root=REPOSITORY_ROOT,
        now=later.generated_at_utc,
    )
    assert promote_staged_signal(later.signal_id, config=config, now=later.generated_at_utc)
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 1


def test_historical_committed_signal_is_retained_as_dead_letter_not_skipped(
    tmp_path: Path,
) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    signal_log = tmp_path / "signals.jsonl"
    _write_signals(signal_log, signal)

    assert (
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=signal.generated_at_utc + timedelta(days=8),
        )
        == 1
    )
    with PipOutbox(config.outbox_path) as outbox:
        summary = outbox.summary()
        assert summary.pending == 0
        assert summary.dead_letter == 1


def test_reconciliation_ignores_only_an_incomplete_final_tail(tmp_path: Path) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    signal_log = tmp_path / "signals.jsonl"
    valid = json.dumps(
        signal.to_mapping(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    signal_log.write_bytes(valid + b"\n" + b'{"signal_id":')

    assert (
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=signal.generated_at_utc + timedelta(seconds=1),
        )
        == 1
    )
    with PipOutbox(config.outbox_path) as outbox:
        assert outbox.summary().pending == 1


def test_reconciliation_fails_closed_on_corrupt_committed_interior_record(
    tmp_path: Path,
) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    signal_log = tmp_path / "signals.jsonl"
    valid = json.dumps(
        signal.to_mapping(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    signal_log.write_bytes(valid + b"\n" + b"not-json\n" + valid + b"\n")

    with pytest.raises(PipExportError, match="corrupt committed Hermes signal log record 2"):
        reconcile_signal_log(
            signal_log,
            config=config,
            repository_root=REPOSITORY_ROOT,
            now=signal.generated_at_utc + timedelta(seconds=1),
        )
