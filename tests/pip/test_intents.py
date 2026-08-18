from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path

from tests.pip.test_contract import REPOSITORY_ROOT, make_signal
from weatherbot.pip import (
    PipExporterConfig,
    PipIntentStore,
    PipOutbox,
    promote_staged_signal,
    reconcile_signal_log,
    stage_signal,
)


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
    signal_log.write_text(
        json.dumps(signal.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
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


def test_historical_committed_signal_is_retained_as_dead_letter_not_skipped(
    tmp_path: Path,
) -> None:
    signal = make_signal()
    config = _config(tmp_path, key_byte=1, key_id="key-1")
    signal_log = tmp_path / "signals.jsonl"
    signal_log.write_text(
        json.dumps(signal.to_mapping(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

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
    valid = json.dumps(signal.to_mapping(), sort_keys=True, separators=(",", ":")).encode("utf-8")
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
