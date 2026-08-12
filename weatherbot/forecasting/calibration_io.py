"""Strict deserialization for reproducible calibration datasets."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from weatherbot.forecasting.calibration import CalibrationError, CalibrationSample, Season
from weatherbot.forecasting.calibration_data import (
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationDatasetRecord,
    ForecastCaptureMethod,
)
from weatherbot.forecasting.model import ForecastSource

_DATASET_SCHEMA_VERSION = 1


def load_calibration_dataset(
    records_path: str | Path,
    manifest_path: str | Path,
) -> CalibrationDataset:
    """Load a canonical dataset and reject any checksum, contract, or ordering drift."""

    records_file = Path(records_path)
    manifest_file = Path(manifest_path)
    try:
        records_text = records_file.read_text(encoding="utf-8")
        manifest_text = manifest_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationError(f"failed to read calibration dataset: {exc}") from exc

    manifest_raw = _json_mapping(manifest_text, label="calibration dataset manifest")
    manifest = _manifest_from_mapping(manifest_raw)
    expected_manifest_sha = _sha256(
        manifest_raw.get("manifest_sha256"),
        label="manifest checksum",
    )
    if manifest.manifest_sha256 != expected_manifest_sha:
        raise CalibrationError("calibration dataset manifest checksum mismatch")

    if not records_text:
        raise CalibrationError("calibration dataset records file is empty")
    if not records_text.endswith("\n"):
        raise CalibrationError("calibration dataset JSONL must end with a newline")

    records: list[CalibrationDatasetRecord] = []
    for line_number, line in enumerate(records_text.splitlines(), start=1):
        if not line:
            raise CalibrationError(f"blank calibration dataset line at {line_number}")
        try:
            decoded: object = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(
                f"invalid calibration dataset JSON on line {line_number}"
            ) from exc
        mapping = _mapping(decoded, label=f"calibration dataset row {line_number}")
        records.append(_record_from_mapping(mapping, line_number=line_number))

    if len(records) != manifest.record_count:
        raise CalibrationError(
            "calibration dataset record count differs from manifest: "
            f"{len(records)} != {manifest.record_count}"
        )

    dataset = CalibrationDataset(records=tuple(records), manifest=manifest)
    canonical_jsonl = dataset.to_jsonl()
    if canonical_jsonl != records_text:
        raise CalibrationError(
            "calibration dataset JSONL is not in canonical deterministic order/serialization"
        )
    actual_dataset_sha = hashlib.sha256(records_text.encode()).hexdigest()
    if actual_dataset_sha != manifest.dataset_sha256:
        raise CalibrationError("calibration dataset checksum mismatch")

    _validate_dataset_manifest_alignment(dataset)
    return dataset


def _record_from_mapping(
    raw: Mapping[str, object],
    *,
    line_number: int,
) -> CalibrationDatasetRecord:
    market_date = _date(raw.get("market_date"), label=f"row {line_number} market_date")
    try:
        forecast_source = ForecastSource(
            _text(raw.get("forecast_source"), label=f"row {line_number} forecast_source")
        )
    except ValueError as exc:
        raise CalibrationError(
            f"unsupported forecast source on calibration dataset line {line_number}"
        ) from exc
    try:
        capture_method = ForecastCaptureMethod(
            _text(
                raw.get("forecast_capture_method"),
                label=f"row {line_number} forecast_capture_method",
            )
        )
    except ValueError as exc:
        raise CalibrationError(
            f"unsupported capture method on calibration dataset line {line_number}"
        ) from exc

    sample = CalibrationSample(
        city=_text(raw.get("city"), label=f"row {line_number} city"),
        climate_region=_text(
            raw.get("climate_region"),
            label=f"row {line_number} climate_region",
        ),
        forecast_source=forecast_source,
        market_date=market_date,
        lead_days=_integer(raw.get("lead_days"), label=f"row {line_number} lead_days"),
        forecast_temperature_f=_decimal(
            raw.get("forecast_temperature_f"),
            label=f"row {line_number} forecast_temperature_f",
        ),
        observed_temperature_f=_decimal(
            raw.get("observed_temperature_f"),
            label=f"row {line_number} observed_temperature_f",
        ),
        forecast_as_of_utc=_timestamp(
            raw.get("forecast_as_of_utc"),
            label=f"row {line_number} forecast_as_of_utc",
        ),
        observation_finalized_at_utc=_timestamp(
            raw.get("observation_finalized_at_utc"),
            label=f"row {line_number} observation_finalized_at_utc",
        ),
        observation_source=_text(
            raw.get("observation_source"),
            label=f"row {line_number} observation_source",
        ),
        station_id=_text(raw.get("station_id"), label=f"row {line_number} station_id"),
        measurement_basis=_text(
            raw.get("measurement_basis"),
            label=f"row {line_number} measurement_basis",
        ),
        forecast_payload_sha256=_sha256(
            raw.get("forecast_payload_sha256"),
            label=f"row {line_number} forecast_payload_sha256",
        ),
        observation_payload_sha256=_sha256(
            raw.get("observation_payload_sha256"),
            label=f"row {line_number} observation_payload_sha256",
        ),
    )
    season = _text(raw.get("season"), label=f"row {line_number} season")
    if season != sample.season.value:
        raise CalibrationError(
            f"calibration dataset line {line_number} season disagrees with market_date"
        )

    return CalibrationDatasetRecord(
        sample=sample,
        forecast_contract_id=_text(
            raw.get("forecast_contract_id"),
            label=f"row {line_number} forecast_contract_id",
        ),
        observation_contract_id=_text(
            raw.get("observation_contract_id"),
            label=f"row {line_number} observation_contract_id",
        ),
        forecast_capture_contract_id=_text(
            raw.get("forecast_capture_contract_id"),
            label=f"row {line_number} forecast_capture_contract_id",
        ),
        forecast_capture_method=capture_method,
        forecast_source_url=_text(
            raw.get("forecast_source_url"),
            label=f"row {line_number} forecast_source_url",
        ),
        forecast_latitude=_decimal(
            raw.get("forecast_latitude"),
            label=f"row {line_number} forecast_latitude",
        ),
        forecast_longitude=_decimal(
            raw.get("forecast_longitude"),
            label=f"row {line_number} forecast_longitude",
        ),
        forecast_bias_correction=_boolean(
            raw.get("forecast_bias_correction"),
            label=f"row {line_number} forecast_bias_correction",
        ),
        forecast_provenance_sha256=_sha256(
            raw.get("forecast_provenance_sha256"),
            label=f"row {line_number} forecast_provenance_sha256",
        ),
    )


def _manifest_from_mapping(raw: Mapping[str, object]) -> CalibrationDatasetManifest:
    schema_version = _integer(raw.get("schema_version"), label="manifest schema_version")
    if schema_version != _DATASET_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported calibration dataset schema: {schema_version}")
    record_count = _integer(raw.get("record_count"), label="manifest record_count")
    if record_count <= 0:
        raise CalibrationError("manifest record_count must be positive")
    capture_contract_ids = _string_tuple(
        raw.get("capture_contract_ids"),
        label="manifest capture_contract_ids",
    )
    if not capture_contract_ids:
        raise CalibrationError("manifest capture_contract_ids must not be empty")
    parity_report_sha256s = _sha_tuple(
        raw.get("parity_report_sha256s"),
        label="manifest parity_report_sha256s",
    )
    start_date = _date(raw.get("start_date"), label="manifest start_date")
    end_date = _date(raw.get("end_date"), label="manifest end_date")
    if start_date > end_date:
        raise CalibrationError("manifest date interval is reversed")
    return CalibrationDatasetManifest(
        forecast_contract_id=_text(
            raw.get("forecast_contract_id"),
            label="manifest forecast_contract_id",
        ),
        observation_contract_id=_text(
            raw.get("observation_contract_id"),
            label="manifest observation_contract_id",
        ),
        record_count=record_count,
        start_date=start_date,
        end_date=end_date,
        capture_contract_ids=capture_contract_ids,
        parity_report_sha256s=parity_report_sha256s,
        dataset_sha256=_sha256(raw.get("dataset_sha256"), label="manifest dataset_sha256"),
    )


def _validate_dataset_manifest_alignment(dataset: CalibrationDataset) -> None:
    manifest = dataset.manifest
    records = dataset.records
    dates = tuple(record.sample.market_date for record in records)
    if min(dates) != manifest.start_date or max(dates) != manifest.end_date:
        raise CalibrationError("dataset date bounds differ from manifest")
    forecast_contracts = {record.forecast_contract_id for record in records}
    if forecast_contracts != {manifest.forecast_contract_id}:
        raise CalibrationError("dataset forecast contracts differ from manifest")
    observation_contracts = {record.observation_contract_id for record in records}
    if observation_contracts != {manifest.observation_contract_id}:
        raise CalibrationError("dataset observation contracts differ from manifest")
    capture_contracts = tuple(
        sorted({record.forecast_capture_contract_id for record in records})
    )
    if capture_contracts != manifest.capture_contract_ids:
        raise CalibrationError("dataset capture contracts differ from manifest")


def _json_mapping(text: str, *, label: str) -> Mapping[str, object]:
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{label} is not valid JSON") from exc
    return _mapping(decoded, label=label)


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CalibrationError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CalibrationError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"{label} must be non-blank text")
    return value.strip()


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalibrationError(f"{label} must be an integer")
    return value


def _boolean(value: object, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise CalibrationError(f"{label} must be boolean")
    return value


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str, float)):
        raise CalibrationError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalibrationError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise CalibrationError(f"{label} must be finite")
    return result


def _timestamp(value: object, *, label: str) -> datetime:
    text = _text(value, label=label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalibrationError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _date(value: object, *, label: str) -> date:
    text = _text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalibrationError(f"{label} must use YYYY-MM-DD") from exc


def _sha256(value: object, *, label: str) -> str:
    text = _text(value, label=label).lower()
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise CalibrationError(f"{label} must be a SHA-256 digest")
    return text


def _string_tuple(value: object, *, label: str) -> tuple[str, ...]:
    items = tuple(_text(item, label=label) for item in _sequence(value, label=label))
    if len(items) != len(set(items)) or items != tuple(sorted(items)):
        raise CalibrationError(f"{label} must be unique and sorted")
    return items


def _sha_tuple(value: object, *, label: str) -> tuple[str, ...]:
    items = tuple(_sha256(item, label=label) for item in _sequence(value, label=label))
    if len(items) != len(set(items)) or items != tuple(sorted(items)):
        raise CalibrationError(f"{label} must be unique and sorted")
    return items
