"""Sparse-tolerant historical calibration sweep for issue #12.

The settlement adapter remains deliberately strict: incomplete Weather Underground station
series are not authoritative evidence. Historical calibration, however, must tolerate real
missing station days without weakening that resolution boundary. This module runs the
existing one-day collector, excludes only explicit station-coverage failures, records those
exclusions with frozen-payload provenance, and merges the remaining canonical records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_build import (
    DEFAULT_MARKETS,
    CalibrationMarketSpec,
    ImmutableHttpCache,
    collect_calibration_dataset,
    parity_report_from_evidence,
)
from weatherbot.forecasting.calibration_data import (
    ArchiveParityReport,
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationDatasetRecord,
    write_calibration_dataset,
)
from weatherbot.resolution.wunderground import WeatherUndergroundHistoryError

_SWEEP_SCHEMA_VERSION = 1
_HORIZON_COUNT = 3
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_REQUEST_DELAY_SECONDS = 0.5
_COVERAGE_ERROR_PREFIXES = (
    "insufficient Weather Underground observations:",
    "Weather Underground series begins too late in the local day:",
    "Weather Underground series ends too early in the local day:",
)


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256_hex(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise CalibrationError(f"{label} must be a SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class CalibrationSweepExclusion:
    city: str
    station_id: str
    market_date: date
    source_url: str
    raw_payload_sha256: str
    reason_code: str
    reason_detail: str

    def __post_init__(self) -> None:
        city = self.city.strip().lower()
        station = self.station_id.strip().upper()
        source_url = self.source_url.strip()
        reason_code = self.reason_code.strip().lower()
        reason_detail = self.reason_detail.strip()
        if not city or not station or not source_url or not reason_code or not reason_detail:
            raise CalibrationError("calibration exclusion text fields must not be blank")
        if not source_url.startswith("https://www.wunderground.com/history/daily/"):
            raise CalibrationError("calibration exclusion must reference Weather Underground history")
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "station_id", station)
        object.__setattr__(
            self,
            "raw_payload_sha256",
            _sha256_hex(self.raw_payload_sha256, label="exclusion raw payload hash"),
        )
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "reason_detail", reason_detail)

    def to_mapping(self) -> dict[str, object]:
        return {
            "city": self.city,
            "station_id": self.station_id,
            "market_date": self.market_date.isoformat(),
            "source_url": self.source_url,
            "raw_payload_sha256": self.raw_payload_sha256,
            "reason_code": self.reason_code,
            "reason_detail": self.reason_detail,
        }


@dataclass(frozen=True, slots=True)
class CalibrationSweepReport:
    requested_start_date: date
    requested_end_date: date
    cities: tuple[str, ...]
    expected_record_count: int
    collected_record_count: int
    exclusions: tuple[CalibrationSweepExclusion, ...]
    dataset_sha256: str

    def __post_init__(self) -> None:
        if self.requested_end_date < self.requested_start_date:
            raise CalibrationError("sweep report date interval is reversed")
        if not self.cities or any(not city.strip() for city in self.cities):
            raise CalibrationError("sweep report requires non-blank cities")
        if self.expected_record_count <= 0 or self.collected_record_count <= 0:
            raise CalibrationError("sweep report record counts must be positive")
        excluded_record_count = len(self.exclusions) * _HORIZON_COUNT
        if self.collected_record_count + excluded_record_count != self.expected_record_count:
            raise CalibrationError("sweep report record accounting is inconsistent")
        object.__setattr__(
            self,
            "dataset_sha256",
            _sha256_hex(self.dataset_sha256, label="sweep dataset hash"),
        )

    @property
    def excluded_record_count(self) -> int:
        return len(self.exclusions) * _HORIZON_COUNT

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": _SWEEP_SCHEMA_VERSION,
            "requested_start_date": self.requested_start_date.isoformat(),
            "requested_end_date": self.requested_end_date.isoformat(),
            "cities": list(self.cities),
            "expected_record_count": self.expected_record_count,
            "collected_record_count": self.collected_record_count,
            "excluded_date_count": len(self.exclusions),
            "excluded_record_count": self.excluded_record_count,
            "dataset_sha256": self.dataset_sha256,
            "exclusions": [item.to_mapping() for item in self.exclusions],
        }

    @property
    def report_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.to_mapping())).hexdigest()

    def to_json(self) -> str:
        payload = dict(self.to_mapping())
        payload["report_sha256"] = self.report_sha256
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _is_coverage_only_error(exc: WeatherUndergroundHistoryError) -> bool:
    detail = str(exc)
    return any(detail.startswith(prefix) for prefix in _COVERAGE_ERROR_PREFIXES)


def _date_range(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise CalibrationError("sweep end date must not precede start date")
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _record_sort_key(record: CalibrationDatasetRecord) -> tuple[object, ...]:
    sample = record.sample
    return (
        sample.market_date,
        sample.city,
        sample.lead_days,
        sample.station_id,
        sample.measurement_basis,
    )


def merge_calibration_datasets(datasets: Iterable[CalibrationDataset]) -> CalibrationDataset:
    """Merge canonical one-day datasets without losing record-level provenance."""

    chunks = tuple(datasets)
    if not chunks:
        raise CalibrationError("calibration sweep produced no usable datasets")
    forecast_contracts = {item.manifest.forecast_contract_id for item in chunks}
    observation_contracts = {item.manifest.observation_contract_id for item in chunks}
    if len(forecast_contracts) != 1 or len(observation_contracts) != 1:
        raise CalibrationError("calibration sweep chunks disagree on source contracts")

    ordered = tuple(sorted((record for item in chunks for record in item.records), key=_record_sort_key))
    if not ordered:
        raise CalibrationError("calibration sweep produced no usable records")
    seen: set[tuple[str, object, date, int, str, str]] = set()
    for record in ordered:
        if record.identity in seen:
            raise CalibrationError(f"duplicate calibration sweep identity: {record.identity}")
        seen.add(record.identity)

    jsonl = "".join(
        json.dumps(record.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
        for record in ordered
    )
    manifest = CalibrationDatasetManifest(
        forecast_contract_id=next(iter(forecast_contracts)),
        observation_contract_id=next(iter(observation_contracts)),
        record_count=len(ordered),
        start_date=ordered[0].sample.market_date,
        end_date=ordered[-1].sample.market_date,
        capture_contract_ids=tuple(
            sorted({record.forecast_capture_contract_id for record in ordered})
        ),
        parity_report_sha256s=tuple(
            sorted(
                {
                    digest
                    for item in chunks
                    for digest in item.manifest.parity_report_sha256s
                }
            )
        ),
        dataset_sha256=hashlib.sha256(jsonl.encode()).hexdigest(),
    )
    return CalibrationDataset(records=ordered, manifest=manifest)


def collect_calibration_sweep(
    *,
    start_date: date,
    end_date: date,
    cache: ImmutableHttpCache,
    markets: tuple[CalibrationMarketSpec, ...],
    parity_report: ArchiveParityReport,
    now_utc: datetime | None = None,
) -> tuple[CalibrationDataset, CalibrationSweepReport]:
    """Collect a sparse-tolerant dataset while excluding only coverage-deficient WU days."""

    if not markets:
        raise CalibrationError("calibration sweep requires at least one market")
    target_dates = _date_range(start_date, end_date)
    chunks: list[CalibrationDataset] = []
    exclusions: list[CalibrationSweepExclusion] = []

    for market in markets:
        for target_date in target_dates:
            try:
                chunk = collect_calibration_dataset(
                    start_date=target_date,
                    end_date=target_date,
                    cache=cache,
                    markets=(market,),
                    parity_report=parity_report,
                    now_utc=now_utc,
                )
            except WeatherUndergroundHistoryError as exc:
                if not _is_coverage_only_error(exc):
                    raise
                history_url = market.history_url(target_date)
                frozen = cache.get(
                    namespace=f"observations/{market.city}",
                    key=target_date.isoformat(),
                    requested_url=history_url,
                    suffix=".html",
                    headers={},
                )
                exclusions.append(
                    CalibrationSweepExclusion(
                        city=market.city,
                        station_id=market.station_id,
                        market_date=target_date,
                        source_url=history_url,
                        raw_payload_sha256=frozen.payload_sha256,
                        reason_code="weather_underground_coverage",
                        reason_detail=str(exc),
                    )
                )
                continue
            if chunk.manifest.record_count != _HORIZON_COUNT:
                raise CalibrationError(
                    f"expected {_HORIZON_COUNT} records for {market.city} {target_date}, "
                    f"got {chunk.manifest.record_count}"
                )
            chunks.append(chunk)

    dataset = merge_calibration_datasets(chunks)
    expected_record_count = len(target_dates) * len(markets) * _HORIZON_COUNT
    report = CalibrationSweepReport(
        requested_start_date=start_date,
        requested_end_date=end_date,
        cities=tuple(market.city for market in markets),
        expected_record_count=expected_record_count,
        collected_record_count=dataset.manifest.record_count,
        exclusions=tuple(
            sorted(exclusions, key=lambda item: (item.market_date, item.city, item.station_id))
        ),
        dataset_sha256=dataset.manifest.dataset_sha256,
    )
    return dataset, report


def _selected_markets(names: list[str] | None) -> tuple[CalibrationMarketSpec, ...]:
    if not names:
        return DEFAULT_MARKETS
    requested = {name.strip().lower() for name in names if name.strip()}
    known = {market.city: market for market in DEFAULT_MARKETS}
    unknown = sorted(requested - set(known))
    if unknown:
        raise CalibrationError(f"unknown calibration cities: {', '.join(unknown)}")
    return tuple(known[name] for name in sorted(requested))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a reproducible sparse-tolerant forecast-calibration dataset"
    )
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/calibration/cache"))
    parser.add_argument("--records-out", type=Path, default=Path("data/calibration/dataset.jsonl"))
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=Path("data/calibration/dataset-manifest.json"),
    )
    parser.add_argument(
        "--exclusions-out",
        type=Path,
        default=Path("data/calibration/dataset-exclusions.json"),
    )
    parser.add_argument(
        "--parity-evidence",
        type=Path,
        required=True,
        help="Committed archive-parity evidence JSON used to authorize the capture contract",
    )
    parser.add_argument("--city", action="append", dest="cities")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=_DEFAULT_REQUEST_DELAY_SECONDS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    parity_report = parity_report_from_evidence(args.parity_evidence)
    cache = ImmutableHttpCache(
        root=args.cache_dir,
        timeout_seconds=args.timeout_seconds,
        request_delay_seconds=args.request_delay_seconds,
        offline=args.offline,
    )
    dataset, report = collect_calibration_sweep(
        start_date=args.start_date,
        end_date=args.end_date,
        cache=cache,
        markets=_selected_markets(args.cities),
        parity_report=parity_report,
        now_utc=datetime.now(UTC),
    )
    write_calibration_dataset(
        dataset,
        records_path=args.records_out,
        manifest_path=args.manifest_out,
    )
    args.exclusions_out.parent.mkdir(parents=True, exist_ok=True)
    args.exclusions_out.write_text(report.to_json(), encoding="utf-8")
    print(
        json.dumps(
            {
                "records": dataset.manifest.record_count,
                "excluded_dates": len(report.exclusions),
                "excluded_records": report.excluded_record_count,
                "start_date": dataset.manifest.start_date.isoformat(),
                "end_date": dataset.manifest.end_date.isoformat(),
                "dataset_sha256": dataset.manifest.dataset_sha256,
                "manifest_sha256": dataset.manifest.manifest_sha256,
                "exclusions_sha256": report.report_sha256,
                "cache_dir": str(args.cache_dir),
                "offline": bool(args.offline),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
