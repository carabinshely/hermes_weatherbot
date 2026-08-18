"""Signal-log reconciliation for exactly one PIP signal.created event per Hermes signal."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

from weatherbot.pip.core import (
    PipExportError,
    canonical_event_bytes,
    load_release,
    signal_to_event,
)
from weatherbot.pip.intents import PipIntentStore
from weatherbot.pip.runtime import (
    PipExporterConfig,
    promote_staged_signal,
    stage_signal,
)
from weatherbot.producer.model import HermesSignal, SignalMarketReference


def signal_from_mapping(value: object) -> HermesSignal:
    """Rehydrate one durable HermesSignal JSONL record with normal model validation."""
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


def _read_committed_signals(signal_log_path: Path) -> list[HermesSignal]:
    try:
        raw = signal_log_path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise PipExportError(
            f"cannot read Hermes signal log for PIP reconciliation: {exc}"
        ) from exc

    records = raw.splitlines(keepends=True)
    if records and not records[-1].endswith((b"\n", b"\r")):
        records = records[:-1]

    signals: list[HermesSignal] = []
    for index, line in enumerate(records, start=1):
        payload = line.rstrip(b"\r\n")
        if not payload:
            continue
        try:
            decoded = payload.decode("utf-8", errors="strict")
            signals.append(signal_from_mapping(json.loads(decoded)))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise PipExportError(
                f"corrupt committed Hermes signal log record {index}: {exc}"
            ) from exc
    return signals


def _intent_matches_signal(
    signal: HermesSignal,
    *,
    key_id: str,
    canonical_bytes: bytes,
    repository_root: Path,
) -> bool:
    release = load_release(repository_root, signal.strategy_version)
    event = signal_to_event(signal, key_id=key_id, release=release)
    return canonical_event_bytes(event) == canonical_bytes


def reconcile_signal_log(
    signal_log_path: Path,
    *,
    config: PipExporterConfig,
    repository_root: Path,
    now: datetime | None = None,
) -> int:
    """Reconcile one immutable PIP create from the first durable occurrence of each signal_id.

    Repeated Hermes scans may emit the same logical ``signal_id`` with different processing-time
    metadata. Lifecycle v1 permits exactly one ``signal.created`` for that signal. The append-only
    JSONL order therefore defines the canonical creation occurrence: the first durable record.

    A pre-commit staging intent is promoted only if its canonical event bytes match that first
    durable occurrence. Otherwise it cannot be the canonical create: it is discarded and the
    first durable occurrence is frozen with current key material. This preserves exact bytes after
    real crashes without allowing a later repeated scan or an uncommitted orphan assertion to
    replace canonical history.
    """
    if not config.enabled:
        return 0
    current = (now or datetime.now(UTC)).astimezone(UTC)
    signals = _read_committed_signals(signal_log_path)
    by_signal: OrderedDict[str, list[HermesSignal]] = OrderedDict()
    for signal in signals:
        by_signal.setdefault(signal.signal_id, []).append(signal)

    # The complete-record parse above is authoritative. Staging rows whose signal_id is absent
    # from that durable set came from attempts that never committed and can now be retired safely.
    with PipIntentStore(config.outbox_path) as intents:
        intents.discard_orphans(set(by_signal))

    promoted = 0
    for signal_id, occurrences in by_signal.items():
        first = occurrences[0]
        with PipIntentStore(config.outbox_path) as intents:
            if intents.has_outbox_signal(signal_id):
                continue

            intent = intents.get(signal_id)
            if intent is not None:
                if _intent_matches_signal(
                    first,
                    key_id=intent.key_id,
                    canonical_bytes=intent.canonical_event_bytes,
                    repository_root=repository_root,
                ):
                    if intents.promote(signal_id, now=current):
                        promoted += 1
                    continue
                # The staged bytes do not represent the first durable occurrence, so they cannot
                # become the one canonical signal.created event for this lifecycle identity.
                intents.discard(signal_id)

        stage_signal(
            first,
            config=config,
            repository_root=repository_root,
            now=current,
        )
        if promote_staged_signal(signal_id, config=config, now=current):
            promoted += 1
    return promoted
