#!/usr/bin/env python3
"""Cross-check Hermes canonical/delivery behavior against pinned PIP authority files."""

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

LOCK_PATH = REPOSITORY_ROOT / "tests/pip/pip-contract.lock.json"


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


def _git_blob_sha(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def check_authority_bundle(authority_dir: Path) -> None:
    lock = _object(_load(LOCK_PATH), label="PIP contract lock")
    if lock.get("commit") != "a21c3e2ec9e7d5fdd453df4d7cbf641989493af8":
        raise RuntimeError("PIP contract lock moved without explicit conformance review")
    bundle = _object(lock.get("authority_bundle"), label="PIP authority bundle")
    authority_root = authority_dir.resolve()
    for repository_path, raw_entry in bundle.items():
        if not isinstance(repository_path, str):
            raise RuntimeError("PIP authority path must be a string")
        entry = _object(raw_entry, label=f"PIP authority entry {repository_path}")
        expected = entry.get("git_blob_sha")
        if not isinstance(expected, str):
            raise RuntimeError(
                f"PIP authority entry {repository_path} lacks git_blob_sha"
            )
        local = (REPOSITORY_ROOT / repository_path).resolve()
        if authority_root not in local.parents:
            raise RuntimeError(
                f"PIP authority file escapes expected directory: {repository_path}"
            )
        actual = _git_blob_sha(local.read_bytes())
        if actual != expected:
            raise RuntimeError(
                f"PIP authority blob mismatch for {repository_path}: "
                f"expected {expected}, got {actual}"
            )


def check_version_support(authority_dir: Path) -> None:
    matrix = _object(
        _load(authority_dir / "version-support-matrix.json"),
        label="PIP version support matrix",
    )
    if matrix.get("contract") != "pip.signal-envelope":
        raise RuntimeError("PIP version support authority names a different contract")
    versions = _list(matrix.get("versions"), label="PIP supported versions")
    version_one = next(
        (
            _object(raw, label="PIP version entry")
            for raw in versions
            if isinstance(raw, dict) and raw.get("version") == "1"
        ),
        None,
    )
    if version_one is None or version_one.get("state") != "active":
        raise RuntimeError("PIP SignalEnvelope v1 is not active in pinned authority")
    support = _object(version_one.get("support"), label="PIP v1 support policy")
    if (
        support.get("allow_new_generation") is not True
        or support.get("allow_new_ingestion") is not True
    ):
        raise RuntimeError("PIP SignalEnvelope v1 is not open for generation/ingestion")


def check_golden_vectors(authority_dir: Path) -> None:
    manifest = _object(
        _load(authority_dir / "golden-manifest.json"),
        label="PIP golden manifest",
    )
    if manifest.get("canonicalization") != "RFC 8785 JCS over top-level event value":
        raise RuntimeError("PIP canonicalization authority changed")
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

        # JCS is defined over I-JSON / binary64 number semantics. A canonical spelling such as
        # 100000000000000000000 may be the serialization of an original binary64 1e20 value;
        # parsing it back as an arbitrary-precision Python int would change the input domain and
        # make a conforming RFC 8785 implementation reject the otherwise valid golden vector.
        parsed = json.loads(expected_raw, parse_int=float, parse_float=float)
        event = _object(parsed, label=f"PIP golden event {source_raw}")
        actual = canonical_event_bytes(event)
        expected = expected_raw.encode("utf-8")
        if actual != expected:
            raise RuntimeError(f"Hermes JCS mismatch for PIP golden vector {source_raw}")
        if len(actual) != expected_length:
            raise RuntimeError(f"Hermes JCS length mismatch for PIP golden vector {source_raw}")
        if hashlib.sha256(actual).hexdigest() != expected_sha:
            raise RuntimeError(f"Hermes JCS digest mismatch for PIP golden vector {source_raw}")


def check_delivery_authority(authority_dir: Path) -> None:
    manifest = _object(
        _load(authority_dir / "delivery-manifest.json"),
        label="PIP delivery manifest",
    )
    if manifest.get("protocol") != "producer-delivery-v1":
        raise RuntimeError("PIP delivery protocol authority changed")
    claim = _object(manifest.get("claim_policy"), label="PIP claim policy")
    if claim.get("maximum_claim_duration_ms") != 60_000:
        raise RuntimeError(
            "Hermes requires the pinned 60-second maximum PIP claim duration"
        )
    response = _object(manifest.get("response_policy"), label="PIP response policy")
    if response.get("max_result_body_bytes") != 65_536:
        raise RuntimeError("Hermes requires the pinned 65536-byte PIP result limit")
    policy = _object(manifest.get("retry_policy"), label="PIP retry policy")
    if (
        policy.get("base_delay_ms") != 1_000
        or policy.get("max_delay_ms") != 60_000
        or policy.get("maximum_supported_delivery_horizon_ms") != 604_800_000
        or policy.get("exponent_cap") != 10
        or policy.get("jitter_lower_ratio") != 0.5
        or policy.get("jitter_upper_ratio") != 1.0
    ):
        raise RuntimeError("Hermes PIP retry constants differ from pinned delivery authority")
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
    parser.add_argument("authority_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    authority_dir = args.authority_dir.resolve()
    check_authority_bundle(authority_dir)
    check_version_support(authority_dir)
    check_golden_vectors(authority_dir)
    check_delivery_authority(authority_dir)
    write_real_fixture(args.output_dir)
    print("Hermes matches vendored authority from pinned private PIP commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
