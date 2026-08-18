"""Deterministic negative evidence for unavailable historical forecast runs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from weatherbot.forecasting.calibration import CalibrationError

_MARKER_SCHEMA_VERSION = 1
_MARKER_NAMESPACE = "_unavailable_forecast_runs"
_OPEN_METEO_HOST = "single-runs-api.open-meteo.com"
_UNAVAILABLE_REASON_PREFIX = "The requested model run is not available. Model: "


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _paths(root: Path, requested_url: str) -> tuple[Path, Path]:
    key = _sha256(requested_url.encode())
    directory = Path(root) / _MARKER_NAMESPACE
    return directory / f"{key}.body", directory / f"{key}.meta.json"


def open_meteo_unavailable_reason(
    *,
    requested_url: str,
    http_status: int,
    body: bytes,
) -> str | None:
    """Return the provider reason only for an explicit Single Runs unavailability response."""

    if http_status != 400 or urlsplit(requested_url).hostname != _OPEN_METEO_HOST:
        return None
    try:
        decoded: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    payload = cast(dict[str, object], decoded)
    reason = payload.get("reason")
    if payload.get("error") is not True or not isinstance(reason, str):
        return None
    reason = reason.strip()
    return reason if reason.startswith(_UNAVAILABLE_REASON_PREFIX) else None


@dataclass(frozen=True, slots=True)
class UnavailableForecastRunEvidence:
    requested_url: str
    final_url: str
    retrieved_at_utc: datetime
    http_status: int
    reason: str
    payload_sha256: str
    payload: bytes

    def __post_init__(self) -> None:
        if self.retrieved_at_utc.tzinfo is None or self.retrieved_at_utc.utcoffset() is None:
            raise CalibrationError("unavailable forecast retrieval time must be timezone-aware")
        if self.http_status != 400:
            raise CalibrationError("unavailable forecast evidence must use HTTP 400")
        reason = open_meteo_unavailable_reason(
            requested_url=self.requested_url,
            http_status=self.http_status,
            body=self.payload,
        )
        if reason is None or reason != self.reason:
            raise CalibrationError("unavailable forecast evidence has an invalid provider reason")
        if _sha256(self.payload) != self.payload_sha256:
            raise CalibrationError("unavailable forecast evidence payload hash mismatch")
        object.__setattr__(self, "retrieved_at_utc", self.retrieved_at_utc.astimezone(UTC))


class ForecastRunUnavailable(CalibrationError):
    """An exact historical model run is explicitly absent from provider storage."""

    def __init__(self, evidence: UnavailableForecastRunEvidence) -> None:
        self.evidence = evidence
        super().__init__(
            f"historical forecast run unavailable for {evidence.requested_url}: {evidence.reason}"
        )


def freeze_unavailable_forecast_run(
    *,
    root: Path,
    requested_url: str,
    final_url: str,
    http_status: int,
    body: bytes,
    retrieved_at_utc: datetime,
) -> UnavailableForecastRunEvidence | None:
    """Freeze an explicit Open-Meteo missing-run response and return its evidence."""

    reason = open_meteo_unavailable_reason(
        requested_url=requested_url,
        http_status=http_status,
        body=body,
    )
    if reason is None:
        return None
    evidence = UnavailableForecastRunEvidence(
        requested_url=requested_url,
        final_url=final_url,
        retrieved_at_utc=retrieved_at_utc,
        http_status=http_status,
        reason=reason,
        payload_sha256=_sha256(body),
        payload=body,
    )
    body_path, metadata_path = _paths(root, requested_url)
    _atomic_write(body_path, body)
    metadata = {
        "schema_version": _MARKER_SCHEMA_VERSION,
        "requested_url": requested_url,
        "final_url": final_url,
        "retrieved_at_utc": evidence.retrieved_at_utc.isoformat(),
        "http_status": http_status,
        "reason": reason,
        "payload_sha256": evidence.payload_sha256,
    }
    _atomic_write(
        metadata_path,
        (json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(),
    )
    return evidence


def load_unavailable_forecast_run(
    *,
    root: Path,
    requested_url: str,
) -> UnavailableForecastRunEvidence | None:
    """Load and verify frozen missing-run evidence, or return ``None`` when absent."""

    body_path, metadata_path = _paths(root, requested_url)
    body_exists = body_path.exists()
    metadata_exists = metadata_path.exists()
    if not body_exists and not metadata_exists:
        return None
    if body_exists != metadata_exists:
        raise CalibrationError("partial unavailable-forecast cache entry exists")
    body = body_path.read_bytes()
    try:
        decoded: object = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError("invalid unavailable-forecast cache metadata") from exc
    if not isinstance(decoded, dict):
        raise CalibrationError("unavailable-forecast cache metadata must be an object")
    metadata = cast(dict[str, object], decoded)
    if metadata.get("schema_version") != _MARKER_SCHEMA_VERSION:
        raise CalibrationError("unsupported unavailable-forecast cache schema")
    cached_requested = metadata.get("requested_url")
    if cached_requested != requested_url:
        raise CalibrationError("unavailable-forecast requested URL mismatch")
    retrieved_raw = metadata.get("retrieved_at_utc")
    if not isinstance(retrieved_raw, str):
        raise CalibrationError("unavailable-forecast retrieval time is invalid")
    try:
        retrieved = datetime.fromisoformat(retrieved_raw)
    except ValueError as exc:
        raise CalibrationError("unavailable-forecast retrieval time is invalid") from exc
    final_url = metadata.get("final_url")
    reason = metadata.get("reason")
    payload_sha256 = metadata.get("payload_sha256")
    http_status = metadata.get("http_status")
    if (
        not isinstance(final_url, str)
        or not isinstance(reason, str)
        or not isinstance(payload_sha256, str)
        or isinstance(http_status, bool)
        or not isinstance(http_status, int)
    ):
        raise CalibrationError("unavailable-forecast cache metadata fields are invalid")
    return UnavailableForecastRunEvidence(
        requested_url=requested_url,
        final_url=final_url,
        retrieved_at_utc=retrieved,
        http_status=http_status,
        reason=reason,
        payload_sha256=payload_sha256,
        payload=body,
    )
