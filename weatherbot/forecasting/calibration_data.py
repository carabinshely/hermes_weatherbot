"""Deterministic, provenance-preserving calibration dataset construction.

Historical forecast reconstructions are never assumed to be production-equivalent.  An
archive capture whose contract differs from the effective production forecast contract
must have a passing overlap parity report before its records may enter a calibration
dataset.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from weatherbot.domain import WeatherObservationEvidence
from weatherbot.forecasting.calibration import CalibrationError, CalibrationSample
from weatherbot.forecasting.model import DailyHighForecast

_DATASET_SCHEMA_VERSION = 1
_PARITY_SCHEMA_VERSION = 1


def _text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise CalibrationError(f"{label} must not be blank")
    return normalized


def _sha256(value: str, *, label: str) -> str:
    normalized = _text(value, label=label).lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise CalibrationError(f"{label} must be a SHA-256 hex digest")
    return normalized


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalibrationError(f"{label} must be an integer")
    return value


def _boolean(value: object, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise CalibrationError(f"{label} must be boolean")
    return value


def _decimal(value: Decimal | int | str | float, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise CalibrationError(f"{label} must be decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalibrationError(f"{label} must be decimal") from exc
    if not result.is_finite():
        raise CalibrationError(f"{label} must be finite")
    return result


def _utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _http_url(value: str, *, label: str) -> str:
    normalized = _text(value, label=label)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CalibrationError(f"{label} must be an HTTP(S) URL")
    return normalized


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _fahrenheit(value: Decimal, unit: str) -> Decimal:
    normalized = unit.strip().upper()
    if normalized == "F":
        return value
    if normalized == "C":
        return value * Decimal("9") / Decimal("5") + Decimal("32")
    raise CalibrationError(f"unsupported observation temperature unit: {unit!r}")


class ForecastCaptureMethod(StrEnum):
    PRODUCTION = "production"
    PREVIOUS_RUNS = "open_meteo_previous_runs"
    SINGLE_RUN = "open_meteo_single_run"
    IMPORTED = "imported"


@dataclass(frozen=True, slots=True)
class ForecastCalibrationEvidence:
    """One forecast value plus the evidence required to prove what was known when."""

    city: str
    climate_region: str
    forecast: DailyHighForecast
    forecast_as_of_utc: datetime
    lead_days: int
    source_contract_id: str
    capture_contract_id: str
    capture_method: ForecastCaptureMethod
    source_url: str
    latitude: Decimal
    longitude: Decimal
    bias_correction: bool
    payload_sha256: str

    def __post_init__(self) -> None:
        city = _text(self.city, label="forecast city").lower()
        region = _text(self.climate_region, label="forecast climate region").lower()
        source_contract = _text(self.source_contract_id, label="forecast source contract")
        capture_contract = _text(self.capture_contract_id, label="forecast capture contract")
        source_url = _http_url(self.source_url, label="forecast source URL")
        latitude = _decimal(self.latitude, label="forecast latitude")
        longitude = _decimal(self.longitude, label="forecast longitude")
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise CalibrationError("forecast latitude is outside [-90, 90]")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise CalibrationError("forecast longitude is outside [-180, 180]")
        bias_correction = _boolean(self.bias_correction, label="bias_correction")
        payload_hash = _sha256(self.payload_sha256, label="forecast payload hash")
        as_of = _utc(self.forecast_as_of_utc, label="forecast as-of time")
        _integer(self.lead_days, label="forecast lead_days")
        if not 0 <= self.lead_days <= 7:
            raise CalibrationError("forecast lead_days must be between 0 and 7")

        timezone = ZoneInfo(self.forecast.market_timezone)
        expected_lead = (self.forecast.market_date - as_of.astimezone(timezone).date()).days
        if expected_lead != self.lead_days:
            raise CalibrationError(
                "forecast lead bucket differs from the market-date/as-of calendar difference"
            )
        if as_of >= self.forecast.valid_until_utc:
            raise CalibrationError("forecast as-of time is not point-in-time before market-day end")
        model_run = self.forecast.model_run_initialized_at_utc
        if model_run is not None and model_run > as_of + timedelta(seconds=5):
            raise CalibrationError("forecast model run is later than the forecast as-of time")
        if self.capture_method is ForecastCaptureMethod.PRODUCTION:
            if self.forecast.retrieved_at_utc > as_of + timedelta(seconds=5):
                raise CalibrationError("production forecast was retrieved after its as-of time")
            if capture_contract != source_contract:
                raise CalibrationError(
                    "production capture contract must equal the effective source contract"
                )

        object.__setattr__(self, "city", city)
        object.__setattr__(self, "climate_region", region)
        object.__setattr__(self, "forecast_as_of_utc", as_of)
        object.__setattr__(self, "source_contract_id", source_contract)
        object.__setattr__(self, "capture_contract_id", capture_contract)
        object.__setattr__(self, "source_url", source_url)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "bias_correction", bias_correction)
        object.__setattr__(self, "payload_sha256", payload_hash)

    @property
    def identity(self) -> tuple[str, str, date, int]:
        return (self.city, self.source_contract_id, self.forecast.market_date, self.lead_days)

    def provenance_mapping(self) -> dict[str, object]:
        return {
            "city": self.city,
            "climate_region": self.climate_region,
            "forecast_source": self.forecast.source.value,
            "market_date": self.forecast.market_date.isoformat(),
            "market_timezone": self.forecast.market_timezone,
            "forecast_temperature_f": format(self.forecast.temperature_f, "f"),
            "forecast_as_of_utc": self.forecast_as_of_utc.isoformat(),
            "lead_days": self.lead_days,
            "model_run_initialized_at_utc": (
                None
                if self.forecast.model_run_initialized_at_utc is None
                else self.forecast.model_run_initialized_at_utc.isoformat()
            ),
            "source_contract_id": self.source_contract_id,
            "capture_contract_id": self.capture_contract_id,
            "capture_method": self.capture_method.value,
            "source_url": self.source_url,
            "latitude": format(self.latitude, "f"),
            "longitude": format(self.longitude, "f"),
            "bias_correction": self.bias_correction,
            "payload_sha256": self.payload_sha256,
        }

    @property
    def provenance_sha256(self) -> str:
        return _canonical_sha256(self.provenance_mapping())


@dataclass(frozen=True, slots=True)
class ArchiveParityPolicy:
    min_pairs: int
    min_reference_coverage: float
    max_mae_f: float
    max_abs_error_f: float

    def __post_init__(self) -> None:
        _integer(self.min_pairs, label="parity min_pairs")
        if self.min_pairs < 2:
            raise CalibrationError("parity min_pairs must be at least two")
        if (
            not math.isfinite(self.min_reference_coverage)
            or not 0 < self.min_reference_coverage <= 1
        ):
            raise CalibrationError("parity reference coverage must be in (0, 1]")
        for label, value in (
            ("max_mae_f", self.max_mae_f),
            ("max_abs_error_f", self.max_abs_error_f),
        ):
            if not math.isfinite(value) or value < 0:
                raise CalibrationError(f"parity {label} must be finite and non-negative")

    def to_mapping(self) -> dict[str, object]:
        return {
            "min_pairs": self.min_pairs,
            "min_reference_coverage": self.min_reference_coverage,
            "max_mae_f": self.max_mae_f,
            "max_abs_error_f": self.max_abs_error_f,
        }


@dataclass(frozen=True, slots=True)
class ArchiveParityReport:
    reference_contract_id: str
    candidate_contract_id: str
    policy: ArchiveParityPolicy
    reference_count: int
    candidate_count: int
    matched_count: int
    reference_coverage: float
    mean_error_f: float
    mae_f: float
    rmse_f: float
    max_abs_error_f: float
    matched_identity_sha256: str

    def __post_init__(self) -> None:
        reference = _text(self.reference_contract_id, label="parity reference contract")
        candidate = _text(self.candidate_contract_id, label="parity candidate contract")
        if reference == candidate:
            raise CalibrationError("parity candidate contract must differ from reference contract")
        for label, value in (
            ("reference_count", self.reference_count),
            ("candidate_count", self.candidate_count),
            ("matched_count", self.matched_count),
        ):
            _integer(value, label=f"parity {label}")
            if value < 0:
                raise CalibrationError(f"parity {label} must be non-negative")
        for label, value in (
            ("reference_coverage", self.reference_coverage),
            ("mean_error_f", self.mean_error_f),
            ("mae_f", self.mae_f),
            ("rmse_f", self.rmse_f),
            ("max_abs_error_f", self.max_abs_error_f),
        ):
            if not math.isfinite(value):
                raise CalibrationError(f"parity {label} must be finite")
        if not 0 <= self.reference_coverage <= 1:
            raise CalibrationError("parity reference coverage must be between zero and one")
        for label, value in (
            ("mae_f", self.mae_f),
            ("rmse_f", self.rmse_f),
            ("max_abs_error_f", self.max_abs_error_f),
        ):
            if value < 0:
                raise CalibrationError(f"parity {label} must be non-negative")
        identity_hash = _sha256(
            self.matched_identity_sha256,
            label="parity matched identity hash",
        )
        object.__setattr__(self, "reference_contract_id", reference)
        object.__setattr__(self, "candidate_contract_id", candidate)
        object.__setattr__(self, "matched_identity_sha256", identity_hash)

    @property
    def compatible(self) -> bool:
        return (
            self.matched_count >= self.policy.min_pairs
            and self.reference_coverage >= self.policy.min_reference_coverage
            and self.mae_f <= self.policy.max_mae_f
            and self.max_abs_error_f <= self.policy.max_abs_error_f
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": _PARITY_SCHEMA_VERSION,
            "reference_contract_id": self.reference_contract_id,
            "candidate_contract_id": self.candidate_contract_id,
            "policy": self.policy.to_mapping(),
            "reference_count": self.reference_count,
            "candidate_count": self.candidate_count,
            "matched_count": self.matched_count,
            "reference_coverage": self.reference_coverage,
            "mean_error_f": self.mean_error_f,
            "mae_f": self.mae_f,
            "rmse_f": self.rmse_f,
            "max_abs_error_f": self.max_abs_error_f,
            "matched_identity_sha256": self.matched_identity_sha256,
            "compatible": self.compatible,
        }

    @property
    def report_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


def compare_archive_parity(
    reference: Iterable[ForecastCalibrationEvidence],
    candidate: Iterable[ForecastCalibrationEvidence],
    *,
    policy: ArchiveParityPolicy,
) -> ArchiveParityReport:
    reference_items = _unique_forecasts(reference, label="parity reference")
    candidate_items = _unique_forecasts(candidate, label="parity candidate")
    if not reference_items or not candidate_items:
        raise CalibrationError("parity comparison requires reference and candidate forecasts")

    reference_contracts = {item.source_contract_id for item in reference_items.values()}
    if len(reference_contracts) != 1:
        raise CalibrationError("parity reference contains multiple effective source contracts")
    reference_capture_contracts = {item.capture_contract_id for item in reference_items.values()}
    if reference_capture_contracts != reference_contracts:
        raise CalibrationError("parity reference must be direct captures of its source contract")
    candidate_effective_contracts = {item.source_contract_id for item in candidate_items.values()}
    if candidate_effective_contracts != reference_contracts:
        raise CalibrationError("parity candidate targets a different effective source contract")
    candidate_capture_contracts = {item.capture_contract_id for item in candidate_items.values()}
    if len(candidate_capture_contracts) != 1:
        raise CalibrationError("parity candidate contains multiple capture contracts")

    reference_contract = next(iter(reference_contracts))
    candidate_contract = next(iter(candidate_capture_contracts))
    if candidate_contract == reference_contract:
        raise CalibrationError("parity candidate must use a distinct reconstruction contract")

    identities = sorted(set(reference_items) & set(candidate_items))
    if not identities:
        raise CalibrationError("parity comparison has no overlapping forecast identities")
    errors = [
        float(
            candidate_items[identity].forecast.temperature_f
            - reference_items[identity].forecast.temperature_f
        )
        for identity in identities
    ]
    matched_identity_sha = hashlib.sha256(
        "\n".join(_identity_text(identity) for identity in identities).encode()
    ).hexdigest()
    return ArchiveParityReport(
        reference_contract_id=reference_contract,
        candidate_contract_id=candidate_contract,
        policy=policy,
        reference_count=len(reference_items),
        candidate_count=len(candidate_items),
        matched_count=len(identities),
        reference_coverage=len(identities) / len(reference_items),
        mean_error_f=sum(errors) / len(errors),
        mae_f=sum(abs(value) for value in errors) / len(errors),
        rmse_f=math.sqrt(sum(value**2 for value in errors) / len(errors)),
        max_abs_error_f=max(abs(value) for value in errors),
        matched_identity_sha256=matched_identity_sha,
    )


def _identity_text(identity: tuple[str, str, date, int]) -> str:
    city, contract, market_date, lead_days = identity
    return f"{city}|{contract}|{market_date.isoformat()}|D+{lead_days}"


def _unique_forecasts(
    values: Iterable[ForecastCalibrationEvidence],
    *,
    label: str,
) -> dict[tuple[str, str, date, int], ForecastCalibrationEvidence]:
    result: dict[tuple[str, str, date, int], ForecastCalibrationEvidence] = {}
    for item in values:
        existing = result.get(item.identity)
        if existing is None:
            result[item.identity] = item
            continue
        if existing.provenance_sha256 == item.provenance_sha256:
            raise CalibrationError(f"duplicate {label} forecast identity: {item.identity}")
        raise CalibrationError(f"conflicting {label} forecast identity: {item.identity}")
    return result


@dataclass(frozen=True, slots=True)
class CalibrationDatasetRecord:
    sample: CalibrationSample
    forecast_contract_id: str
    observation_contract_id: str
    forecast_capture_contract_id: str
    forecast_capture_method: ForecastCaptureMethod
    forecast_source_url: str
    forecast_latitude: Decimal
    forecast_longitude: Decimal
    forecast_bias_correction: bool
    forecast_provenance_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "forecast_contract_id",
            _text(self.forecast_contract_id, label="dataset forecast contract"),
        )
        object.__setattr__(
            self,
            "observation_contract_id",
            _text(self.observation_contract_id, label="dataset observation contract"),
        )
        object.__setattr__(
            self,
            "forecast_capture_contract_id",
            _text(self.forecast_capture_contract_id, label="dataset capture contract"),
        )
        object.__setattr__(
            self,
            "forecast_source_url",
            _http_url(self.forecast_source_url, label="dataset forecast source URL"),
        )
        object.__setattr__(
            self,
            "forecast_latitude",
            _decimal(self.forecast_latitude, label="dataset forecast latitude"),
        )
        object.__setattr__(
            self,
            "forecast_longitude",
            _decimal(self.forecast_longitude, label="dataset forecast longitude"),
        )
        bias_correction = _boolean(
            self.forecast_bias_correction,
            label="dataset forecast_bias_correction",
        )
        object.__setattr__(self, "forecast_bias_correction", bias_correction)
        object.__setattr__(
            self,
            "forecast_provenance_sha256",
            _sha256(
                self.forecast_provenance_sha256,
                label="dataset forecast provenance hash",
            ),
        )

    @property
    def identity(self) -> tuple[str, object, date, int, str, str]:
        return self.sample.identity

    def to_mapping(self) -> dict[str, object]:
        sample = self.sample
        return {
            "city": sample.city,
            "climate_region": sample.climate_region,
            "forecast_source": sample.forecast_source.value,
            "market_date": sample.market_date.isoformat(),
            "season": sample.season.value,
            "lead_days": sample.lead_days,
            "forecast_temperature_f": format(sample.forecast_temperature_f, "f"),
            "observed_temperature_f": format(sample.observed_temperature_f, "f"),
            "forecast_as_of_utc": sample.forecast_as_of_utc.isoformat(),
            "observation_finalized_at_utc": sample.observation_finalized_at_utc.isoformat(),
            "observation_source": sample.observation_source,
            "station_id": sample.station_id,
            "measurement_basis": sample.measurement_basis,
            "forecast_payload_sha256": sample.forecast_payload_sha256,
            "observation_payload_sha256": sample.observation_payload_sha256,
            "forecast_contract_id": self.forecast_contract_id,
            "observation_contract_id": self.observation_contract_id,
            "forecast_capture_contract_id": self.forecast_capture_contract_id,
            "forecast_capture_method": self.forecast_capture_method.value,
            "forecast_source_url": self.forecast_source_url,
            "forecast_latitude": format(self.forecast_latitude, "f"),
            "forecast_longitude": format(self.forecast_longitude, "f"),
            "forecast_bias_correction": self.forecast_bias_correction,
            "forecast_provenance_sha256": self.forecast_provenance_sha256,
        }

    @property
    def record_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True, slots=True)
class CalibrationDatasetManifest:
    forecast_contract_id: str
    observation_contract_id: str
    record_count: int
    start_date: date
    end_date: date
    capture_contract_ids: tuple[str, ...]
    parity_report_sha256s: tuple[str, ...]
    dataset_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": _DATASET_SCHEMA_VERSION,
            "forecast_contract_id": self.forecast_contract_id,
            "observation_contract_id": self.observation_contract_id,
            "record_count": self.record_count,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "capture_contract_ids": list(self.capture_contract_ids),
            "parity_report_sha256s": list(self.parity_report_sha256s),
            "dataset_sha256": self.dataset_sha256,
        }

    @property
    def manifest_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())

    def to_json(self) -> str:
        payload = dict(self.to_mapping())
        payload["manifest_sha256"] = self.manifest_sha256
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass(frozen=True, slots=True)
class CalibrationDataset:
    records: tuple[CalibrationDatasetRecord, ...]
    manifest: CalibrationDatasetManifest

    @property
    def samples(self) -> tuple[CalibrationSample, ...]:
        return tuple(record.sample for record in self.records)

    def to_jsonl(self) -> str:
        return "".join(
            json.dumps(
                record.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            + "\n"
            for record in self.records
        )


def build_calibration_dataset(
    pairs: Iterable[tuple[ForecastCalibrationEvidence, WeatherObservationEvidence]],
    *,
    forecast_contract_id: str,
    observation_contract_id: str,
    parity_reports: Iterable[ArchiveParityReport] = (),
) -> CalibrationDataset:
    effective_contract = _text(forecast_contract_id, label="dataset forecast contract")
    observation_contract = _text(observation_contract_id, label="dataset observation contract")
    reports = tuple(parity_reports)
    accepted_archive_contracts: dict[str, ArchiveParityReport] = {}
    for report in reports:
        if report.reference_contract_id != effective_contract:
            raise CalibrationError("parity report references a different production contract")
        if not report.compatible:
            raise CalibrationError(
                f"archive parity failed for capture contract {report.candidate_contract_id}"
            )
        previous = accepted_archive_contracts.get(report.candidate_contract_id)
        if previous is not None and previous.report_sha256 != report.report_sha256:
            raise CalibrationError(
                f"multiple parity reports exist for capture contract {report.candidate_contract_id}"
            )
        accepted_archive_contracts[report.candidate_contract_id] = report

    records: list[CalibrationDatasetRecord] = []
    for forecast, observation in pairs:
        if forecast.source_contract_id != effective_contract:
            raise CalibrationError("forecast evidence targets a different source contract")
        if (
            forecast.capture_contract_id != effective_contract
            and forecast.capture_contract_id not in accepted_archive_contracts
        ):
            raise CalibrationError(
                "archive forecast cannot enter the dataset without passing source parity"
            )
        sample = calibration_sample_from_evidence(forecast, observation)
        records.append(
            CalibrationDatasetRecord(
                sample=sample,
                forecast_contract_id=effective_contract,
                observation_contract_id=observation_contract,
                forecast_capture_contract_id=forecast.capture_contract_id,
                forecast_capture_method=forecast.capture_method,
                forecast_source_url=forecast.source_url,
                forecast_latitude=forecast.latitude,
                forecast_longitude=forecast.longitude,
                forecast_bias_correction=forecast.bias_correction,
                forecast_provenance_sha256=forecast.provenance_sha256,
            )
        )

    if not records:
        raise CalibrationError("calibration dataset is empty")
    ordered = tuple(
        sorted(
            records,
            key=lambda item: (
                item.sample.market_date,
                item.sample.city,
                item.sample.lead_days,
                item.sample.station_id,
                item.sample.measurement_basis,
            ),
        )
    )
    seen: dict[tuple[str, object, date, int, str, str], CalibrationDatasetRecord] = {}
    for record in ordered:
        existing = seen.get(record.identity)
        if existing is None:
            seen[record.identity] = record
            continue
        if existing.record_sha256 == record.record_sha256:
            raise CalibrationError(f"duplicate calibration dataset identity: {record.identity}")
        raise CalibrationError(f"conflicting calibration dataset identity: {record.identity}")

    jsonl = "".join(
        json.dumps(record.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
        for record in ordered
    )
    dataset_hash = hashlib.sha256(jsonl.encode()).hexdigest()
    used_capture_contracts = tuple(
        sorted({record.forecast_capture_contract_id for record in ordered})
    )
    used_parity_hashes = tuple(
        sorted(
            accepted_archive_contracts[contract].report_sha256
            for contract in used_capture_contracts
            if contract != effective_contract
        )
    )
    manifest = CalibrationDatasetManifest(
        forecast_contract_id=effective_contract,
        observation_contract_id=observation_contract,
        record_count=len(ordered),
        start_date=ordered[0].sample.market_date,
        end_date=ordered[-1].sample.market_date,
        capture_contract_ids=used_capture_contracts,
        parity_report_sha256s=used_parity_hashes,
        dataset_sha256=dataset_hash,
    )
    return CalibrationDataset(records=ordered, manifest=manifest)


def calibration_sample_from_evidence(
    forecast: ForecastCalibrationEvidence,
    observation: WeatherObservationEvidence,
) -> CalibrationSample:
    if not observation.learning_eligible:
        raise CalibrationError("calibration observation must be final or revised")
    if observation.market_date != forecast.forecast.market_date:
        raise CalibrationError("forecast and observation use different market dates")
    if observation.market_timezone != forecast.forecast.market_timezone:
        raise CalibrationError("forecast and observation use different market timezones")
    finalized = _utc(observation.retrieved_at, label="observation retrieval time")
    if forecast.forecast_as_of_utc >= finalized:
        raise CalibrationError("forecast as-of time must predate finalized observation evidence")
    observed_f = _fahrenheit(observation.temperature, observation.unit)
    return CalibrationSample(
        city=forecast.city,
        climate_region=forecast.climate_region,
        forecast_source=forecast.forecast.source,
        market_date=forecast.forecast.market_date,
        lead_days=forecast.lead_days,
        forecast_temperature_f=forecast.forecast.temperature_f,
        observed_temperature_f=observed_f,
        forecast_as_of_utc=forecast.forecast_as_of_utc,
        observation_finalized_at_utc=finalized,
        observation_source=observation.source_name,
        station_id=observation.station_id,
        measurement_basis=observation.measurement_basis,
        forecast_payload_sha256=forecast.payload_sha256,
        observation_payload_sha256=observation.payload_hash,
    )


def write_calibration_dataset(
    dataset: CalibrationDataset,
    *,
    records_path: str | Path,
    manifest_path: str | Path,
) -> None:
    records_target = Path(records_path)
    manifest_target = Path(manifest_path)
    records_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    records_target.write_text(dataset.to_jsonl(), encoding="utf-8")
    manifest_target.write_text(dataset.manifest.to_json(), encoding="utf-8")


def parity_report_json(report: ArchiveParityReport) -> str:
    payload = dict(report.to_mapping())
    payload["report_sha256"] = report.report_sha256
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_dataset_jsonl(text: str) -> Sequence[Mapping[str, object]]:
    """Parse JSONL structurally for audits without silently accepting non-object rows."""
    rows: list[Mapping[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded: object = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"dataset line {line_number} is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise CalibrationError(f"dataset line {line_number} must be a JSON object")
        rows.append(cast(Mapping[str, object], decoded))
    return tuple(rows)
