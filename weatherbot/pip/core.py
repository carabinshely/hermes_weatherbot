"""PIP SignalEnvelope v1 mapping, canonicalization, and application-identity signing."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from weatherbot.producer.model import HermesSignal

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_PRODUCER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STRATEGY_VERSION_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_RELEASE_REPOSITORY = "carabinshely/hermes_weatherbot"


class PipExportError(RuntimeError):
    """Raised when a real Hermes signal cannot be safely exported to PIP."""


@dataclass(frozen=True, slots=True)
class FrozenEnvelope:
    producer_id: str
    event_id: str
    signal_id: str
    generated_at: datetime
    key_id: str
    event_sha256: str
    canonical_event_bytes: bytes
    envelope_bytes: bytes


@dataclass(frozen=True, slots=True)
class ProducerRelease:
    strategy_version: str
    repository: str
    revision: str
    decision_code_identity: str
    manifest_sha256: str


def canonical_decimal(value: Decimal) -> str:
    """Render a Decimal using SignalEnvelope v1's canonical decimal-string grammar."""
    if not value.is_finite():
        raise PipExportError("PIP decimal values must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", ""}:
        text = "0"
    if text.startswith("+"):
        text = text[1:]
    return text


def canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PipExportError("PIP timestamps must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_sha256(value: str, *, label: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise PipExportError(f"{label} must be lowercase SHA-256 hex")
    return value


def _require_identifier(value: str, *, label: str, producer: bool = False) -> str:
    pattern = _PRODUCER_RE if producer else _IDENTIFIER_RE
    if not pattern.fullmatch(value):
        raise PipExportError(f"{label} is not a valid PIP identifier")
    return value


def make_event_id(signal: HermesSignal) -> str:
    payload = f"signal.created\0{signal.producer_id}\0{signal.signal_id}".encode()
    return f"pevt_{hashlib.sha256(payload).hexdigest()}"


def load_release(repository_root: Path, strategy_version: str) -> ProducerRelease:
    if not _STRATEGY_VERSION_FILE_RE.fullmatch(strategy_version):
        raise PipExportError("strategy_version is not safe for immutable release lookup")
    path = repository_root / "config" / "producer-releases" / f"{strategy_version}.json"
    try:
        raw = path.read_bytes()
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise PipExportError(
            f"immutable producer release manifest unavailable for strategy {strategy_version!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise PipExportError("producer release manifest must be a JSON object")
    data = cast(dict[str, object], parsed)
    expected = {
        "release_manifest_version",
        "strategy_version",
        "repository",
        "revision",
        "decision_code_identity",
    }
    if set(data) != expected:
        raise PipExportError("producer release manifest has unexpected fields")
    if data["release_manifest_version"] != "1":
        raise PipExportError("unsupported producer release manifest version")
    if data["strategy_version"] != strategy_version:
        raise PipExportError("producer release manifest strategy_version mismatch")
    repository = str(data["repository"]).strip()
    revision = str(data["revision"]).strip()
    identity = str(data["decision_code_identity"]).strip()
    if repository != _RELEASE_REPOSITORY:
        raise PipExportError("producer release manifest repository mismatch")
    if not _GIT_COMMIT_RE.fullmatch(revision):
        raise PipExportError("producer release revision must be an immutable lowercase Git commit SHA")
    if not identity:
        raise PipExportError("producer release manifest contains blank decision-code identity")
    return ProducerRelease(
        strategy_version=strategy_version,
        repository=repository,
        revision=revision,
        decision_code_identity=identity,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _market_reference_evidence(signal: HermesSignal, *, observed_at: str) -> tuple[dict[str, str], str]:
    """Build and digest the exact executable-reference evidence Hermes can reproduce.

    Polymarket's provider ``hash`` is preserved as source provenance but is not assumed to be a
    SHA-256 value. PIP requires a SHA-256 content digest, so Hermes computes one over a stable,
    producer-owned JSON projection of the executable reference used for the decision.
    """
    reference = signal.market_reference
    projection = {
        "schema": "hermes.executable-market-reference.v1",
        "condition_id": signal.condition_id,
        "token_id": signal.token_id,
        "observed_at": observed_at,
        "kind": reference.kind,
        "reference_notional": canonical_decimal(reference.reference_notional),
        "best_bid": canonical_decimal(reference.best_bid),
        "best_ask": canonical_decimal(reference.best_ask),
        "average_reference_price": canonical_decimal(reference.average_reference_price),
        "all_in_reference_price": canonical_decimal(reference.all_in_reference_price),
        "worst_reference_price": canonical_decimal(reference.worst_reference_price),
        "probability_edge": canonical_decimal(reference.probability_edge),
        "expected_return": canonical_decimal(reference.expected_return),
        "provider_order_book_hash": reference.order_book_hash,
        "quote_fingerprint": reference.quote_fingerprint,
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return projection, hashlib.sha256(encoded).hexdigest()


def signal_to_event(
    signal: HermesSignal,
    *,
    key_id: str,
    release: ProducerRelease,
) -> dict[str, Any]:
    """Map the immutable public HermesSignal authority into one signal.created event."""
    if release.strategy_version != signal.strategy_version:
        raise PipExportError("release manifest does not match Hermes signal strategy_version")
    producer_id = _require_identifier(signal.producer_id, label="producer_id", producer=True)
    event_id = _require_identifier(make_event_id(signal), label="event_id")
    signal_id = _require_identifier(signal.signal_id, label="signal_id")
    key_id = _require_identifier(key_id.strip(), label="key_id")
    generated_at = canonical_timestamp(signal.generated_at_utc)
    observed_at = canonical_timestamp(signal.market_reference.observed_at_utc)
    if signal.market_reference.observed_at_utc > signal.generated_at_utc:
        raise PipExportError("market reference cannot post-date signal generation")

    model_digest = _require_sha256(signal.artifact_sha256, label="model artifact digest")
    policy_digest = _require_sha256(signal.policy_fingerprint, label="producer policy digest")
    reference_evidence, reference_digest = _market_reference_evidence(signal, observed_at=observed_at)

    probability = canonical_decimal(signal.model_probability)
    executable_price = canonical_decimal(signal.market_reference.all_in_reference_price)
    edge = canonical_decimal(signal.market_reference.probability_edge)
    if Decimal(probability) <= 0 or Decimal(probability) >= 1:
        raise PipExportError("Hermes probability is outside SignalEnvelope v1 bounds")
    if Decimal(executable_price) < 0 or Decimal(executable_price) > 1:
        raise PipExportError("market reference value is outside SignalEnvelope v1 bounds")

    extension = {
        "source": {
            "contract": signal.contract,
            "schema_version": signal.schema_version,
            "policy_fingerprint": signal.policy_fingerprint,
            "event_id": signal.event_id,
            "gamma_market_id": signal.market_id,
            "condition_id": signal.condition_id,
            "token_id": signal.token_id,
            "outcome": signal.outcome,
            "question": signal.question,
        },
        "location": {
            "city_slug": signal.city_slug,
            "city_name": signal.city_name,
            "climate_region": signal.climate_region,
            "market_date": signal.market_date.isoformat(),
            "market_timezone": signal.market_timezone,
            "lead_days": signal.lead_days,
        },
        "forecast": {
            "temperature_f": canonical_decimal(signal.forecast_temperature_f),
            "bucket_key": signal.bucket_key,
            "bucket_label": signal.bucket_label,
        },
        "calibration": {
            "calibration_fingerprint": signal.calibration_fingerprint,
            "weather_fingerprint": signal.weather_fingerprint,
            "forecast_source": signal.forecast_source,
            "calibration_group_key": signal.calibration_group_key,
            "fallback_level": signal.fallback_level,
            "distribution_type": signal.distribution_type,
            "calibration_sample_count": signal.calibration_sample_count,
            "training_cutoff": signal.training_cutoff.isoformat(),
        },
        "market_reference": {
            "reference_notional": canonical_decimal(signal.market_reference.reference_notional),
            "best_bid": canonical_decimal(signal.market_reference.best_bid),
            "best_ask": canonical_decimal(signal.market_reference.best_ask),
            "average_reference_price": canonical_decimal(
                signal.market_reference.average_reference_price
            ),
            "all_in_reference_price": executable_price,
            "worst_reference_price": canonical_decimal(
                signal.market_reference.worst_reference_price
            ),
            "expected_return": canonical_decimal(signal.market_reference.expected_return),
            "order_book_hash": signal.market_reference.order_book_hash,
            "quote_fingerprint": signal.market_reference.quote_fingerprint,
            "evidence_schema": reference_evidence["schema"],
        },
        "eligibility": {
            "producer_public_claim": "not_asserted",
            "producer_paid_claim": "not_asserted",
        },
    }

    return {
        "contract": "pip.signal-envelope",
        "schema_version": "1",
        "event_type": "signal.created",
        "event_id": event_id,
        "signal_id": signal_id,
        "strategy": {
            "producer_id": producer_id,
            "strategy_id": _require_identifier(signal.strategy_id, label="strategy_id"),
            "strategy_version": signal.strategy_version,
        },
        "generated_at": generated_at,
        "market": {
            "venue": _require_identifier(signal.venue, label="venue"),
            "market_id": _require_identifier(signal.condition_id, label="market_id"),
            "outcome_id": _require_identifier(signal.token_id, label="outcome_id"),
        },
        "forecast": {"probability": probability},
        "market_references": {
            "decision_book": {
                "kind": "executable",
                "observed_at": observed_at,
                "value": executable_price,
                "evidence_ref": "decision_book",
            }
        },
        "decision": {
            "classification": signal.classification,
            "edge": edge,
        },
        "artifacts": {
            "model": {
                "kind": "model",
                "id": "hermes_weatherbot.calibration_model",
                "version": signal.model_version,
                "digest": {"algorithm": "sha256", "hex": model_digest},
            },
            "producer_policy": {
                "kind": "configuration",
                "id": "hermes_weatherbot.producer_policy",
                "version": signal.strategy_version,
                "digest": {"algorithm": "sha256", "hex": policy_digest},
            },
            "producer_release": {
                "kind": "code",
                "id": "hermes_weatherbot.producer_release",
                "revision": release.revision,
                "digest": {"algorithm": "sha256", "hex": release.manifest_sha256},
                "reference": f"github://{release.repository}@{release.revision}",
            },
        },
        "evidence": {
            "mode": "live",
            "references": {
                "decision_book": {
                    "type": "hermes.executable_market_reference.v1",
                    "digest": {"algorithm": "sha256", "hex": reference_digest},
                    "observed_at": observed_at,
                }
            },
        },
        "extensions": {f"{producer_id}:weather:v1": extension},
        "authentication": {
            "key_id": key_id,
            "algorithm": "ed25519",
            "encoding": "base64url",
        },
    }


def canonical_event_bytes(event: dict[str, Any]) -> bytes:
    try:
        import rfc8785
    except ImportError as exc:  # pragma: no cover - dependency-profile failure path
        raise PipExportError(
            "PIP export requires rfc8785; install requirements-pip-export.txt"
        ) from exc
    try:
        return rfc8785.dumps(event)
    except Exception as exc:
        raise PipExportError(f"RFC 8785 canonicalization failed: {exc}") from exc


def _decode_unpadded_base64url(value: str, *, label: str) -> bytes:
    if not value or "=" in value or re.search(r"[^A-Za-z0-9_-]", value):
        raise PipExportError(f"{label} must use canonical unpadded base64url")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except ValueError as exc:
        raise PipExportError(f"{label} is not valid base64url") from exc
    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise PipExportError(f"{label} is not canonical base64url")
    return decoded


def load_private_key(path: Path) -> object:
    """Load one raw 32-byte Ed25519 private application-identity key from a secret file."""
    try:
        if os.name == "posix":
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                raise PipExportError("PIP signing key file must not be group/world accessible")
        text = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise PipExportError(f"cannot read PIP signing key: {exc}") from exc
    raw = _decode_unpadded_base64url(text, label="PIP signing private key")
    if len(raw) != 32:
        raise PipExportError("PIP Ed25519 private key must contain exactly 32 raw bytes")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - dependency-profile failure path
        raise PipExportError(
            "PIP export requires cryptography; install requirements-pip-export.txt"
        ) from exc
    return Ed25519PrivateKey.from_private_bytes(raw)


def freeze_signal_envelope(
    signal: HermesSignal,
    *,
    key_id: str,
    private_key: object,
    release: ProducerRelease,
) -> FrozenEnvelope:
    event = signal_to_event(signal, key_id=key_id, release=release)
    canonical = canonical_event_bytes(event)
    signer = getattr(private_key, "sign", None)
    if not callable(signer):
        raise PipExportError("PIP signer does not expose an Ed25519 sign operation")
    signature_raw = signer(canonical)
    if not isinstance(signature_raw, bytes) or len(signature_raw) != 64:
        raise PipExportError("PIP signer returned an invalid Ed25519 signature")
    signature = base64.urlsafe_b64encode(signature_raw).decode("ascii").rstrip("=")
    envelope = {"event": event, "signature": signature}
    envelope_bytes = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    event_sha256 = hashlib.sha256(canonical).hexdigest()
    return FrozenEnvelope(
        producer_id=signal.producer_id,
        event_id=str(event["event_id"]),
        signal_id=signal.signal_id,
        generated_at=signal.generated_at_utc.astimezone(UTC),
        key_id=key_id,
        event_sha256=event_sha256,
        canonical_event_bytes=canonical,
        envelope_bytes=envelope_bytes,
    )
