from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.producer.test_boundary import candidate, policy
from tests.quoting.helpers import NOW, order_book
from weatherbot.pip.core import (
    PipExportError,
    canonical_decimal,
    canonical_timestamp,
    freeze_signal_envelope,
    load_release,
    make_event_id,
    signal_to_event,
)
from weatherbot.producer.model import HermesSignal
from weatherbot.producer.service import evaluate_candidate

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def make_signal(evaluated_at: datetime = NOW) -> HermesSignal:
    signal, evaluation = evaluate_candidate(candidate(), policy(), evaluated_at=evaluated_at)
    assert evaluation.accepted
    assert signal is not None
    return signal


def test_signal_created_mapping_uses_real_public_authority() -> None:
    signal = make_signal()
    release = load_release(REPOSITORY_ROOT, signal.strategy_version)
    event = signal_to_event(signal, key_id="producer-key-test", release=release)

    assert event["event_type"] == "signal.created"
    assert event["signal_id"] == signal.signal_id
    assert event["event_id"] == make_event_id(signal)
    assert event["market"] == {
        "venue": signal.venue,
        "market_id": signal.condition_id,
        "outcome_id": signal.token_id,
    }
    assert event["forecast"] == {"probability": canonical_decimal(signal.model_probability)}
    reference = event["market_references"]["decision_book"]
    assert reference["kind"] == "executable"
    assert reference["value"] == canonical_decimal(signal.market_reference.all_in_reference_price)
    assert reference["observed_at"] == canonical_timestamp(signal.market_reference.observed_at_utc)
    assert event["decision"]["classification"] == "accepted"
    assert event["evidence"]["mode"] == "live"
    extension = event["extensions"]["hermes_weatherbot:weather:v1"]
    assert extension["eligibility"] == {
        "producer_public_claim": "not_asserted",
        "producer_paid_claim": "not_asserted",
    }
    serialized = repr(event).lower()
    for forbidden in ("wallet", "bankroll", "positions", "pnl", "order_id", "shares_to_buy"):
        assert forbidden not in serialized


def test_provider_book_hash_is_preserved_without_being_treated_as_sha256() -> None:
    provider_hash = "provider-book-hash:v7"
    non_sha_candidate = replace(
        candidate(),
        decision_book=order_book(
            first_size="100",
            second_size="100",
            book_hash=provider_hash,
        ),
    )
    signal, evaluation = evaluate_candidate(non_sha_candidate, policy(), evaluated_at=NOW)
    assert evaluation.accepted
    assert signal is not None

    release = load_release(REPOSITORY_ROOT, signal.strategy_version)
    event = signal_to_event(signal, key_id="producer-key-test", release=release)
    extension = event["extensions"]["hermes_weatherbot:weather:v1"]
    assert extension["market_reference"]["order_book_hash"] == provider_hash

    evidence = event["evidence"]["references"]["decision_book"]
    assert evidence["type"] == "hermes.executable_market_reference.v1"
    digest = evidence["digest"]["hex"]
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def test_release_manifest_rejects_mutable_revision(tmp_path: Path) -> None:
    release_dir = tmp_path / "config" / "producer-releases"
    release_dir.mkdir(parents=True)
    (release_dir / "1.json").write_text(
        json.dumps(
            {
                "release_manifest_version": "1",
                "strategy_version": "1",
                "repository": "carabinshely/hermes_weatherbot",
                "revision": "main",
                "decision_code_identity": "weatherbot.producer.service.evaluate_candidate",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PipExportError, match="immutable lowercase Git commit SHA"):
        load_release(tmp_path, "1")


def test_release_lookup_rejects_path_like_strategy_version(tmp_path: Path) -> None:
    with pytest.raises(PipExportError, match="safe for immutable release lookup"):
        load_release(tmp_path, "../1")


def test_freeze_signs_only_canonical_event_and_is_deterministic() -> None:
    signal = make_signal()
    release = load_release(REPOSITORY_ROOT, signal.strategy_version)
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)

    first = freeze_signal_envelope(
        signal,
        key_id="producer-key-test",
        private_key=private_key,
        release=release,
    )
    second = freeze_signal_envelope(
        signal,
        key_id="producer-key-test",
        private_key=private_key,
        release=release,
    )

    assert first.event_sha256 == hashlib.sha256(first.canonical_event_bytes).hexdigest()
    assert first.envelope_bytes == second.envelope_bytes
    assert first.event_sha256 == second.event_sha256

    envelope = json.loads(first.envelope_bytes)
    signature = base64.urlsafe_b64decode(envelope["signature"] + "==")
    private_key.public_key().verify(signature, first.canonical_event_bytes)


def test_wire_scalar_renderers_match_signal_envelope_v1() -> None:
    from decimal import Decimal

    assert canonical_decimal(Decimal("0.4200")) == "0.42"
    assert canonical_decimal(Decimal("1.000")) == "1"
    assert canonical_decimal(Decimal("-0.000")) == "0"
    assert canonical_timestamp(NOW).endswith("Z")
    assert canonical_timestamp(NOW).count(".") == 1
    assert len(canonical_timestamp(NOW).split(".", 1)[1].removesuffix("Z")) == 6
