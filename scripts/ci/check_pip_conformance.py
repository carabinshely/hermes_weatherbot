#!/usr/bin/env python3
"""Cross-check Hermes canonical/delivery behavior against a pinned PIP contract checkout."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.pip.test_contract import REPOSITORY_ROOT, make_signal
from weatherbot.pip import canonical_event_bytes, freeze_signal_envelope, load_release
from weatherbot.pip.runtime import retry_delay_bounds


def _object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be a JSON array")
    return cast(list[object], value)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def check_golden_vectors(contract_root: Path) -> None:
    manifest_path = contract_root / "packages/signal-contract/tests/golden/manifest.json"
    manifest = _object(_load(manifest_path), label="PIP golden manifest")
    for raw_vector in _list(manifest.get("vectors"), label="PIP golden vectors"):
        vector = _object(raw_vector, label="PIP golden vector")
        source_raw = vector.get("source")
        expected_raw = vector.get("canonical_event")
        expected_sha = vector.get("sha256")
        expected_length = vector.get("byte_length")
        if not isinstance(source_raw, str) or not isinstance(expected_raw, str):
            raise RuntimeError("PIP golden vector source/canonical_event must be strings")
        if not isinstance(expected_sha, str) or not isinstance(expected_length, int):
            raise RuntimeError("PIP golden vector digest/length are invalid")
        source = (manifest_path.parent / source_raw).resolve()
        envelope = _object(_load(source), label=f"PIP golden envelope {source_raw}")
        event = _object(envelope.get("event"), label=f"PIP golden event {source_raw}")
        actual = canonical_event_bytes(event)
        expected = expected_raw.encode("utf-8")
        if actual != expected:
            raise RuntimeError(f"Hermes JCS mismatch for PIP golden vector {source_raw}")
        if len(actual) != expected_length:
            raise RuntimeError(f"Hermes JCS length mismatch for PIP golden vector {source_raw}")
        if hashlib.sha256(actual).hexdigest() != expected_sha:
            raise RuntimeError(f"Hermes JCS digest mismatch for PIP golden vector {source_raw}")


def check_retry_vectors(contract_root: Path) -> None:
    path = contract_root / "packages/signal-contract/tests/delivery/manifest.json"
    manifest = _object(_load(path), label="PIP delivery manifest")
    policy = _object(manifest.get("retry_policy"), label="PIP retry policy")
    attempts = _list(policy.get("attempt_vectors"), label="PIP retry attempt vectors")
    for raw_attempt in attempts:
        attempt = _object(raw_attempt, label="PIP retry attempt")
        count = attempt.get("attempt_count")
        minimum_ms = attempt.get("minimum_ms")
        maximum_ms = attempt.get("maximum_ms")
        if (
            not isinstance(count, int)
            or not isinstance(minimum_ms, int)
            or not isinstance(maximum_ms, int)
        ):
            raise RuntimeError("PIP retry vector has invalid numeric fields")
        lower, upper = retry_delay_bounds(count)
        if int(lower * 1000) != minimum_ms or int(upper * 1000) != maximum_ms:
            raise RuntimeError(f"Hermes retry bounds mismatch PIP attempt vector {count}")


def write_real_fixture(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    signal = make_signal()
    private_key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
    frozen = freeze_signal_envelope(
        signal,
        key_id="hermes-conformance-key",
        private_key=private_key,
        release=load_release(REPOSITORY_ROOT, signal.strategy_version),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_text = base64.urlsafe_b64encode(public_raw).decode("ascii").rstrip("=")
    (output_dir / "envelope.json").write_bytes(frozen.envelope_bytes)
    (output_dir / "canonical-event.bin").write_bytes(frozen.canonical_event_bytes)
    (output_dir / "public-key.txt").write_text(public_text, encoding="ascii")
    (output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "producer_id": frozen.producer_id,
                "event_id": frozen.event_id,
                "signal_id": frozen.signal_id,
                "event_sha256": frozen.event_sha256,
                "key_id": frozen.key_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract_root", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    check_golden_vectors(args.contract_root)
    check_retry_vectors(args.contract_root)
    write_real_fixture(args.output_dir)
    print("Hermes/PIP canonical and retry vectors match pinned authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
