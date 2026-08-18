#!/usr/bin/env python3
"""Validate a real Hermes-produced envelope using the pinned PIP conformance environment."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import jsonschema
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from scripts.conformance.__main__ import CONTRACT, normative_failure, strict_json


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _decode_base64url(value: str, *, length: int) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    decoded = base64.urlsafe_b64decode(value + padding)
    if len(decoded) != length:
        raise RuntimeError(f"base64url value must decode to {length} bytes")
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise RuntimeError("base64url value is not canonical")
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture_dir", type=Path)
    args = parser.parse_args()

    raw = (args.fixture_dir / "envelope.json").read_bytes()
    envelope = strict_json(raw)
    schema = json.loads(
        (CONTRACT / "schema/signal-envelope-v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(envelope)
    failure = normative_failure(envelope)
    if failure is not None:
        raise RuntimeError(f"PIP normative validation failed: {failure}")

    event = _object(envelope.get("event"), label="envelope.event")
    canonical = rfc8785.dumps(event)
    expected_canonical = (args.fixture_dir / "canonical-event.bin").read_bytes()
    if canonical != expected_canonical:
        raise RuntimeError("PIP canonical event bytes differ from Hermes frozen bytes")

    metadata = _object(
        json.loads((args.fixture_dir / "metadata.json").read_text(encoding="utf-8")),
        label="fixture metadata",
    )
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != metadata.get("event_sha256"):
        raise RuntimeError("PIP event digest differs from Hermes frozen event digest")

    signature_raw = envelope.get("signature")
    if not isinstance(signature_raw, str):
        raise RuntimeError("Hermes fixture is missing detached signature")
    signature = _decode_base64url(signature_raw, length=64)
    public_text = (args.fixture_dir / "public-key.txt").read_text(encoding="ascii").strip()
    public_key = Ed25519PublicKey.from_public_bytes(_decode_base64url(public_text, length=32))
    public_key.verify(signature, canonical)

    auth = _object(event.get("authentication"), label="event.authentication")
    if auth.get("key_id") != metadata.get("key_id"):
        raise RuntimeError("PIP-authenticated key_id differs from Hermes frozen key_id")

    print("Pinned PIP authority accepts the real Hermes signal.created envelope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
