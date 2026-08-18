"""PIP exporter configuration, signal-log reconciliation, and bounded HTTP delivery."""

from __future__ import annotations

import json
import os
import random
import socket
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import requests

from weatherbot.pip.core import (
    PipExportError,
    freeze_signal_envelope,
    load_private_key,
    load_release,
)
from weatherbot.pip.outbox import OutboxItem, PipOutbox
from weatherbot.producer.model import HermesSignal, SignalMarketReference

MAX_RESULT_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class PipExporterConfig:
    enabled: bool
    endpoint: str
    outbox_path: Path
    signing_key_path: Path | None
    key_id: str | None
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0


def _env_bool(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PipExportError(f"{name} must be a boolean value")


def load_exporter_config(repository_root: Path) -> PipExporterConfig:
    enabled = _env_bool("HERMES_PIP_EXPORT_ENABLED")
    endpoint = os.getenv("HERMES_PIP_ENDPOINT", "").strip()
    outbox_raw = os.getenv("HERMES_PIP_OUTBOX_PATH", "state/pip-outbox.sqlite3").strip()
    key_path_raw = os.getenv("HERMES_PIP_SIGNING_KEY_PATH", "").strip()
    key_id = os.getenv("HERMES_PIP_KEY_ID", "").strip() or None
    outbox_path = Path(outbox_raw)
    if not outbox_path.is_absolute():
        outbox_path = repository_root / outbox_path
    key_path = Path(key_path_raw) if key_path_raw else None
    if key_path is not None and not key_path.is_absolute():
        key_path = repository_root / key_path
    config = PipExporterConfig(
        enabled=enabled,
        endpoint=endpoint,
        outbox_path=outbox_path,
        signing_key_path=key_path,
        key_id=key_id,
    )
    if enabled:
        _validate_enabled_config(config)
    return config


def _validate_enabled_config(config: PipExporterConfig) -> None:
    if not config.endpoint:
        raise PipExportError("HERMES_PIP_ENDPOINT is required when PIP export is enabled")
    parsed = urlparse(config.endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PipExportError("PIP endpoint must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise PipExportError("non-local PIP endpoints must use HTTPS")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise PipExportError("PIP endpoint must not contain query, fragment, or userinfo")
    if config.signing_key_path is None or config.key_id is None:
        raise PipExportError("PIP signing key path and immutable key ID are required")
    if config.max_delay_seconds < config.base_delay_seconds or config.base_delay_seconds <= 0:
        raise PipExportError("invalid PIP retry-delay configuration")


def enqueue_signal(
    signal: HermesSignal,
    *,
    config: PipExporterConfig,
    repository_root: Path,
) -> bool:
    """Durably enqueue one already-made signal without opening a network request."""
    if not config.enabled:
        return False
    assert config.signing_key_path is not None
    assert config.key_id is not None
    release = load_release(repository_root, signal.strategy_version)
    private_key = load_private_key(config.signing_key_path)
    frozen = freeze_signal_envelope(
        signal,
        key_id=config.key_id,
        private_key=private_key,
        release=release,
    )
    with PipOutbox(config.outbox_path) as outbox:
        outbox.enqueue(frozen)
    return True


def _signal_from_mapping(value: object) -> HermesSignal:
    if not isinstance(value, dict):
        raise PipExportError("Hermes signal log record must be an object")
    data = cast(dict[str, object], value)
    market_reference_raw = data.get("market_reference")
    if not isinstance(market_reference_raw, dict):
        raise PipExportError("Hermes signal log has invalid market_reference")
    ref = cast(dict[str, object], market_reference_raw)
    reference = SignalMarketReference(
        kind=str(ref["kind"]),
        order_book_hash=str(ref["order_book_hash"]),
        observed_at_utc=datetime.fromisoformat(str(ref["observed_at_utc"])),
        reference_notional=Decimal(str(ref["reference_notional"])),
        best_bid=Decimal(str(ref["best_bid"])),
        best_ask=Decimal(str(ref["best_ask"])),
        average_reference_price=Decimal(str(ref["average_reference_price"])),
        all_in_reference_price=Decimal(str(ref["all_in_reference_price"])),
        worst_reference_price=Decimal(str(ref["worst_reference_price"])),
        probability_edge=Decimal(str(ref["probability_edge"])),
        expected_return=Decimal(str(ref["expected_return"])),
        quote_fingerprint=str(ref["quote_fingerprint"]),
    )
    return HermesSignal(
        signal_id=str(data["signal_id"]),
        producer_id=str(data["producer_id"]),
        strategy_id=str(data["strategy_id"]),
        strategy_version=str(data["strategy_version"]),
        policy_fingerprint=str(data["policy_fingerprint"]),
        generated_at_utc=datetime.fromisoformat(str(data["generated_at_utc"])),
        venue=str(data["venue"]),
        event_id=str(data["event_id"]),
        market_id=str(data["market_id"]),
        condition_id=str(data["condition_id"]),
        outcome=str(data["outcome"]),
        token_id=str(data["token_id"]),
        question=str(data["question"]),
        city_slug=str(data["city_slug"]),
        city_name=str(data["city_name"]),
        climate_region=str(data["climate_region"]),
        lead_days=int(cast(int, data["lead_days"])),
        market_date=date.fromisoformat(str(data["market_date"])),
        market_timezone=str(data["market_timezone"]),
        bucket_key=str(data["bucket_key"]),
        bucket_label=str(data["bucket_label"]),
        forecast_temperature_f=Decimal(str(data["forecast_temperature_f"])),
        model_probability=Decimal(str(data["model_probability"])),
        classification=str(data["classification"]),
        market_reference=reference,
        model_version=str(data["model_version"]),
        artifact_sha256=str(data["artifact_sha256"]),
        calibration_fingerprint=str(data["calibration_fingerprint"]),
        weather_fingerprint=str(data["weather_fingerprint"]),
        forecast_source=str(data["forecast_source"]),
        calibration_group_key=str(data["calibration_group_key"]),
        fallback_level=str(data["fallback_level"]),
        distribution_type=str(data["distribution_type"]),
        calibration_sample_count=int(cast(int, data["calibration_sample_count"])),
        training_cutoff=date.fromisoformat(str(data["training_cutoff"])),
        contract=str(data.get("contract", "hermes.signal")),
        schema_version=str(data.get("schema_version", "1")),
    )


def reconcile_signal_log(
    signal_log_path: Path,
    *,
    config: PipExporterConfig,
    repository_root: Path,
) -> int:
    """Idempotently recover missing outbox rows from complete durable JSONL records."""
    if not config.enabled:
        return 0
    try:
        raw = signal_log_path.read_bytes()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise PipExportError(
            f"cannot read Hermes signal log for PIP reconciliation: {exc}"
        ) from exc
    records = raw.splitlines(keepends=True)
    if records and not records[-1].endswith((b"\n", b"\r")):
        records = records[:-1]
    enqueued = 0
    for index, line in enumerate(records, start=1):
        payload = line.rstrip(b"\r\n")
        if not payload:
            continue
        try:
            decoded = payload.decode("utf-8", errors="strict")
            parsed = json.loads(decoded)
            signal = _signal_from_mapping(parsed)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise PipExportError(
                f"corrupt committed Hermes signal log record {index}: {exc}"
            ) from exc
        if signal.generated_at_utc.astimezone(UTC) + timedelta(days=7) <= datetime.now(UTC):
            continue
        enqueue_signal(signal, config=config, repository_root=repository_root)
        enqueued += 1
    return enqueued


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    disposition: str
    result_class: str
    receipt_id: str | None = None
    reason_code: str | None = None
    retry_after_ms: int | None = None


def _bounded_body(response: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    response.raw.decode_content = True
    for chunk in response.raw.stream(8192, decode_content=True):
        if not isinstance(chunk, bytes):
            raise PipExportError("PIP response stream produced non-bytes content")
        total += len(chunk)
        if total > MAX_RESULT_BYTES:
            raise PipExportError("PIP result body exceeds 65536 decoded bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_bound_result(body: bytes, item: OutboxItem) -> DeliveryResult:
    try:
        parsed = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipExportError("PIP returned malformed delivery-result JSON") from exc
    if not isinstance(parsed, dict):
        raise PipExportError("PIP delivery result must be an object")
    data = cast(dict[str, object], parsed)
    if data.get("contract") != "pip.event-delivery-result" or data.get("protocol_version") != "1":
        raise PipExportError("PIP returned an unknown delivery-result contract/version")
    event = data.get("event")
    if not isinstance(event, dict):
        raise PipExportError("PIP delivery result is missing its event binding")
    binding = cast(dict[str, object], event)
    if set(binding) != {"producer_id", "event_id", "event_sha256"}:
        raise PipExportError("PIP delivery result has an invalid event binding")
    if (
        binding["producer_id"] != item.producer_id
        or binding["event_id"] != item.event_id
        or binding["event_sha256"] != item.event_sha256
    ):
        raise PipExportError("PIP delivery result does not bind to the attempted event")
    disposition = data.get("disposition")
    if disposition in {"accepted", "already_accepted"}:
        if set(data) != {"contract", "protocol_version", "disposition", "event", "receipt_id"}:
            raise PipExportError("PIP acceptance result contains unexpected fields")
        receipt = data.get("receipt_id")
        if not isinstance(receipt, str) or not receipt:
            raise PipExportError("PIP acceptance result has no receipt_id")
        return DeliveryResult(str(disposition), str(disposition), receipt_id=receipt)
    if disposition == "retry":
        allowed = {
            "contract",
            "protocol_version",
            "disposition",
            "event",
            "reason_code",
            "retry_after_ms",
        }
        if not set(data) <= allowed or "reason_code" not in data:
            raise PipExportError("PIP retry result has invalid fields")
        reason = data.get("reason_code")
        retry_after = data.get("retry_after_ms")
        if not isinstance(reason, str) or not reason:
            raise PipExportError("PIP retry result has invalid reason_code")
        if retry_after is not None and (
            isinstance(retry_after, bool)
            or not isinstance(retry_after, int)
            or not 0 <= retry_after <= 86_400_000
        ):
            raise PipExportError("PIP retry result has invalid retry_after_ms")
        return DeliveryResult("retry", "retry", reason_code=reason, retry_after_ms=retry_after)
    if disposition == "rejected":
        required = {
            "contract",
            "protocol_version",
            "disposition",
            "event",
            "category",
            "reason_code",
        }
        if set(data) != required:
            raise PipExportError("PIP rejection result has invalid fields")
        category = data.get("category")
        reason = data.get("reason_code")
        allowed_categories = {
            "invalid_envelope",
            "authentication",
            "lifecycle",
            "identity_conflict",
            "unsupported_version",
            "payload_limit",
            "policy",
            "other",
        }
        if category not in allowed_categories or not isinstance(reason, str) or not reason:
            raise PipExportError("PIP rejection result has invalid category/reason")
        return DeliveryResult("rejected", f"rejected:{category}", reason_code=reason)
    raise PipExportError("PIP delivery result has an unknown disposition")


def _retry_delay(
    item: OutboxItem, config: PipExporterConfig, result: DeliveryResult | None
) -> float:
    exponent = min(max(item.attempt_count - 1, 0), 30)
    raw = min(config.max_delay_seconds, config.base_delay_seconds * (2**exponent))
    delay = random.uniform(raw / 2, raw)
    if result is not None and result.retry_after_ms is not None:
        delay = max(delay, result.retry_after_ms / 1000)
    return min(delay, config.max_delay_seconds)


def _deliver_claimed(
    *,
    outbox: PipOutbox,
    item: OutboxItem,
    config: PipExporterConfig,
    client: requests.Session,
    now: datetime,
) -> None:
    """Deliver one already-claimed item using only its persisted frozen bytes."""
    try:
        response = client.post(
            config.endpoint,
            data=item.envelope_bytes,
            headers={"Content-Type": "application/json"},
            allow_redirects=False,
            stream=True,
            timeout=(5, 20),
        )
    except requests.RequestException as exc:
        delay = _retry_delay(item, config, None)
        outbox.retry(
            item,
            next_attempt_at=now + timedelta(seconds=delay),
            result_class=f"network:{type(exc).__name__}",
            http_status=None,
            now=now,
        )
        return

    status = response.status_code
    try:
        if 300 <= status < 400:
            raise PipExportError("PIP redirects are never followed")
        body = _bounded_body(response)
        result = _parse_bound_result(body, item)
    except PipExportError as exc:
        delay = _retry_delay(item, config, None)
        outbox.retry(
            item,
            next_attempt_at=now + timedelta(seconds=delay),
            result_class=f"ambiguous:{type(exc).__name__}",
            http_status=status,
            now=now,
        )
        return
    finally:
        response.close()

    if result.disposition in {"accepted", "already_accepted"}:
        assert result.receipt_id is not None
        outbox.acknowledge(
            item,
            receipt_id=result.receipt_id,
            result_class=result.result_class,
            http_status=status,
            now=now,
        )
        return
    if result.disposition == "rejected":
        outbox.dead_letter(
            item,
            reason=result.reason_code or "pip.rejected",
            result_class=result.result_class,
            http_status=status,
            now=now,
        )
        return
    delay = _retry_delay(item, config, result)
    outbox.retry(
        item,
        next_attempt_at=now + timedelta(seconds=delay),
        result_class=result.result_class,
        http_status=status,
        now=now,
    )


def deliver_once(
    *,
    config: PipExporterConfig,
    owner_id: str | None = None,
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> bool:
    """Recover, claim, and make at most one bounded automatic PIP delivery attempt."""
    if not config.enabled:
        return False
    current = (now or datetime.now(UTC)).astimezone(UTC)
    worker = owner_id or f"{socket.gethostname()}:{os.getpid()}"
    owned_session = session is None
    client = session or requests.Session()
    try:
        with PipOutbox(config.outbox_path) as outbox:
            outbox.recover(now=current)
            item = outbox.claim_due(owner_id=worker, now=current)
            if item is None:
                return False
            _deliver_claimed(outbox=outbox, item=item, config=config, client=client, now=current)
            return True
    finally:
        if owned_session:
            client.close()


def deliver_dead_letter_once(
    *,
    config: PipExporterConfig,
    event_id: str,
    operator_id: str,
    reason: str,
    owner_id: str | None = None,
    session: requests.Session | None = None,
    now: datetime | None = None,
) -> bool:
    """Perform one explicit audited delivery attempt for a retained dead letter."""
    if not config.enabled:
        return False
    current = (now or datetime.now(UTC)).astimezone(UTC)
    worker = owner_id or f"{socket.gethostname()}:{os.getpid()}"
    owned_session = session is None
    client = session or requests.Session()
    try:
        with PipOutbox(config.outbox_path) as outbox:
            outbox.recover(now=current)
            item = outbox.claim_dead_letter_once(
                event_id=event_id,
                owner_id=worker,
                operator_id=operator_id,
                reason=reason,
                now=current,
            )
            _deliver_claimed(outbox=outbox, item=item, config=config, client=client, now=current)
            return True
    finally:
        if owned_session:
            client.close()
