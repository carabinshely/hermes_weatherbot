#!/usr/bin/env python3
"""Validate a real Hermes envelope against the vendored pinned PIP v1 authority."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import jsonschema
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

BLOCKED_COMMAND_KEYS = {
    "execution_command",
    "order_command",
    "transaction_command",
    "wallet_private_key",
    "wallet_seed",
    "seed_phrase",
    "exchange_write_credential",
    "trading_credential",
}
BLOCKED_STRING_MARKERS = (
    "exchange-write-credential://",
    "wallet-seed:",
    "wallet-private-key:",
)
REVIEW_STRING_MARKERS = (
    "unclassified-authority://",
    "unclassified-key://",
)


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate member: {key}")
        result[key] = value
    return result


def _strict_json(raw: bytes) -> dict[str, Any]:
    """Match the pinned PIP conformance kit's strict UTF-8/I-JSON parser."""
    text = raw.decode("utf-8", errors="strict")
    if text.startswith("\ufeff"):
        raise ValueError("UTF-8 BOM forbidden")

    def checked_int(value: str) -> int:
        parsed = int(value)
        if abs(parsed) > 9_007_199_254_740_991:
            raise ValueError("integer outside I-JSON range")
        return parsed

    def checked_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("number outside I-JSON range")
        return parsed

    value = json.loads(
        text,
        object_pairs_hook=_reject_duplicates,
        parse_int=checked_int,
        parse_float=checked_float,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-JSON number token: {token}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("envelope must be a JSON object")

    def reject_surrogates(item: Any) -> None:
        if isinstance(item, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise ValueError("lone surrogate")
        elif isinstance(item, dict):
            for key, child in item.items():
                reject_surrogates(key)
                reject_surrogates(child)
        elif isinstance(item, list):
            for child in item:
                reject_surrogates(child)

    reject_surrogates(value)
    return value


def _decode_base64url(value: str, *, length: int) -> bytes:
    if "=" in value:
        raise RuntimeError("base64url value must be unpadded")
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("invalid base64url value") from exc
    if len(decoded) != length:
        raise RuntimeError(f"base64url value must decode to {length} bytes")
    if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise RuntimeError("base64url value is not canonical")
    return decoded


def _parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _security_failure(envelope: dict[str, Any]) -> tuple[str, str] | None:
    """Match the bounded Issue #28 security rules used by the pinned PIP kit."""

    def inspect(item: Any) -> tuple[str, str] | None:
        if isinstance(item, dict):
            for key, value in item.items():
                if key.lower() in BLOCKED_COMMAND_KEYS:
                    return "security.prohibited_capability", "NOEXEC_PROHIBITED_EVENT_CONTENT"
                nested = inspect(value)
                if nested is not None:
                    return nested
        elif isinstance(item, list):
            for value in item:
                nested = inspect(value)
                if nested is not None:
                    return nested
        elif isinstance(item, str):
            lowered = item.lower()
            if any(marker in lowered for marker in BLOCKED_STRING_MARKERS):
                return "security.prohibited_capability", "NOEXEC_PROHIBITED_EVENT_CONTENT"
            if any(marker in lowered for marker in REVIEW_STRING_MARKERS):
                return "security.review_required", "NOEXEC_AMBIGUOUS_AUTHORITY"
        return None

    event_failure = inspect(envelope["event"])
    if event_failure is not None:
        return event_failure

    signature = envelope.get("signature")
    if isinstance(signature, str):
        try:
            padding = "=" * ((4 - len(signature) % 4) % 4)
            decoded = base64.urlsafe_b64decode(signature + padding).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            decoded = ""
        lowered = decoded.lower()
        if any(marker in lowered for marker in BLOCKED_STRING_MARKERS):
            return "security.prohibited_capability", "NOEXEC_PROHIBITED_SECRET_TRANSPORT"
        if any(marker in lowered for marker in REVIEW_STRING_MARKERS):
            return "security.review_required", "NOEXEC_AMBIGUOUS_SECRET_AUTHORITY"
    return None


def _normative_failure(envelope: dict[str, Any]) -> tuple[str, str] | None:
    """Match the pinned PIP v1 cross-field and no-execution checks relevant to ingestion."""
    event = _object(envelope["event"], label="envelope.event")
    strategy = _object(event["strategy"], label="event.strategy")
    producer_id = strategy["producer_id"]
    if not isinstance(producer_id, str):
        return "envelope.invalid", "PRODUCER_IDENTIFIER_REQUIRED"

    generated_value = event["generated_at"]
    if not isinstance(generated_value, str):
        return "envelope.invalid", "CALENDAR_VALID_TIMESTAMP_REQUIRED"
    try:
        generated_at = _parse_instant(generated_value)
    except ValueError:
        return "envelope.invalid", "CALENDAR_VALID_TIMESTAMP_REQUIRED"

    extensions = _object(event.get("extensions", {}), label="event.extensions")
    for namespace in extensions:
        if not namespace.startswith(f"{producer_id}:"):
            return "envelope.invalid", "EXTENSION_OWNER_MUST_MATCH_PRODUCER"

    evidence = _object(event["evidence"], label="event.evidence")
    references = _object(evidence["references"], label="event.evidence.references")
    for raw_evidence in references.values():
        evidence_item = _object(raw_evidence, label="evidence reference")
        observed = evidence_item.get("observed_at")
        if not isinstance(observed, str):
            return "envelope.invalid", "CALENDAR_VALID_TIMESTAMP_REQUIRED"
        try:
            if _parse_instant(observed) > generated_at:
                return "envelope.invalid", "EVIDENCE_CANNOT_LOOK_AHEAD"
        except ValueError:
            return "envelope.invalid", "CALENDAR_VALID_TIMESTAMP_REQUIRED"

    market_references = _object(
        event.get("market_references", {}),
        label="event.market_references",
    )
    for raw_market_reference in market_references.values():
        market_reference = _object(raw_market_reference, label="market reference")
        observed = market_reference.get("observed_at")
        if not isinstance(observed, str):
            return "envelope.invalid", "CALENDAR_VALID_TIMESTAMP_REQUIRED"
        try:
            if _parse_instant(observed) > generated_at:
                return "envelope.invalid", "MARKET_REFERENCE_CANNOT_LOOK_AHEAD"
        except ValueError:
            return "envelope.invalid", "CALENDAR_VALID_TIMESTAMP_REQUIRED"
        evidence_ref = market_reference.get("evidence_ref")
        if evidence_ref is not None and evidence_ref not in references:
            return "envelope.invalid", "MARKET_EVIDENCE_REFERENCE_MUST_RESOLVE"

    return _security_failure(envelope)


def _check_version_support(authority_dir: Path, envelope: dict[str, Any]) -> None:
    matrix = _object(_load(authority_dir / "version-support-matrix.json"), label="version matrix")
    event = _object(envelope["event"], label="event")
    version = event.get("schema_version")
    if not isinstance(version, str):
        raise RuntimeError("SignalEnvelope schema_version must be an explicit string")
    versions = matrix.get("versions")
    if not isinstance(versions, list):
        raise RuntimeError("PIP version matrix lacks versions")
    match = next(
        (
            _object(raw, label="version entry")
            for raw in versions
            if isinstance(raw, dict) and raw.get("version") == version
        ),
        None,
    )
    if match is None or match.get("state") not in {"active", "deprecated"}:
        raise RuntimeError(f"PIP SignalEnvelope version {version} is not ingestible")
    support = _object(match.get("support"), label="version support")
    if support.get("allow_new_ingestion") is not True:
        raise RuntimeError(f"PIP SignalEnvelope version {version} forbids new ingestion")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("authority_dir", type=Path)
    parser.add_argument("fixture_dir", type=Path)
    args = parser.parse_args()

    raw = (args.fixture_dir / "envelope.json").read_bytes()
    envelope = _strict_json(raw)
    schema = _object(
        _load(args.authority_dir / "signal-envelope-v1.schema.json"),
        label="SignalEnvelope schema",
    )
    jsonschema.Draft202012Validator(schema).validate(envelope)
    _check_version_support(args.authority_dir, envelope)
    failure = _normative_failure(envelope)
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
    if auth.get("algorithm") != "ed25519" or auth.get("encoding") != "base64url":
        raise RuntimeError("Hermes fixture uses the wrong PIP signing suite")
    if auth.get("key_id") != metadata.get("key_id"):
        raise RuntimeError("PIP-authenticated key_id differs from Hermes frozen key_id")

    print("Vendored pinned PIP authority accepts the real Hermes signal.created envelope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
