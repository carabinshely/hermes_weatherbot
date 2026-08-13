"""Calibration sweep with auditable observation and forecast source-gap exclusions."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from weatherbot.domain import MarketId
from weatherbot.forecasting.archive import (
    calibration_run_for_market_day,
    parse_single_run_daily_highs,
    single_run_url,
)
from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_build import (
    DEFAULT_MARKETS,
    OBSERVATION_CONTRACT_ID,
    CalibrationMarketSpec,
    ImmutableHttpCache,
    _wunderground_headers,
    collect_calibration_dataset,
    parity_report_from_evidence,
)
from weatherbot.forecasting.calibration_data import (
    ArchiveParityReport,
    CalibrationDataset,
    build_calibration_dataset,
    write_calibration_dataset,
)
from weatherbot.forecasting.calibration_sweep import merge_calibration_datasets
from weatherbot.resolution.wunderground import (
    WeatherUndergroundHistoryError,
    parse_wunderground_daily_history_html,
)

_SCHEMA_VERSION = 2
_HORIZONS = (0, 1, 2)
_COVERAGE_ERROR_PREFIXES = (
    "insufficient Weather Underground observations:",
    "Weather Underground series begins too late in the local day:",
    "Weather Underground series ends too early in the local day:",
)
_EXPECTED_PROVIDER = "Open-Meteo Single Runs API"
_EXPECTED_MODEL = "ecmwf_ifs025"
_UNAVAILABLE_REASON_PREFIX = "The requested model run is not available. Model: ecmwf_ifs025, run: "


def _canonical_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_coverage_only_error(exc: WeatherUndergroundHistoryError) -> bool:
    detail = str(exc)
    return any(detail.startswith(prefix) for prefix in _COVERAGE_ERROR_PREFIXES)


def _date_range(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise CalibrationError("sweep end date must not precede start date")
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


@dataclass(frozen=True, slots=True)
class UnavailableRunEvidence:
    run_initialized_at_utc: datetime
    source_url: str
    response_body_sha256: str
    reason: str
    workflow_run: int


@dataclass(frozen=True, slots=True)
class CalibrationSourceGap:
    city: str
    station_id: str
    market_date: date
    lead_days: int | None
    source_class: str
    source_url: str
    evidence_source_url: str
    raw_payload_sha256: str
    reason_code: str
    reason_detail: str
    run_initialized_at_utc: datetime | None = None

    @property
    def excluded_record_count(self) -> int:
        return 3 if self.lead_days is None else 1

    def to_mapping(self) -> dict[str, object]:
        return {
            "city": self.city,
            "station_id": self.station_id,
            "market_date": self.market_date.isoformat(),
            "lead_days": self.lead_days,
            "source_class": self.source_class,
            "source_url": self.source_url,
            "evidence_source_url": self.evidence_source_url,
            "raw_payload_sha256": self.raw_payload_sha256,
            "reason_code": self.reason_code,
            "reason_detail": self.reason_detail,
            "run_initialized_at_utc": (
                self.run_initialized_at_utc.isoformat()
                if self.run_initialized_at_utc is not None
                else None
            ),
            "excluded_record_count": self.excluded_record_count,
        }


@dataclass(frozen=True, slots=True)
class CalibrationSparseReport:
    requested_start_date: date
    requested_end_date: date
    cities: tuple[str, ...]
    expected_record_count: int
    collected_record_count: int
    exclusions: tuple[CalibrationSourceGap, ...]
    dataset_sha256: str

    def __post_init__(self) -> None:
        excluded = sum(item.excluded_record_count for item in self.exclusions)
        if self.collected_record_count + excluded != self.expected_record_count:
            raise CalibrationError("sparse calibration record accounting is inconsistent")

    @property
    def excluded_record_count(self) -> int:
        return sum(item.excluded_record_count for item in self.exclusions)

    @property
    def excluded_date_count(self) -> int:
        return sum(item.lead_days is None for item in self.exclusions)

    @property
    def forecast_gap_sample_count(self) -> int:
        return sum(item.lead_days is not None for item in self.exclusions)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "requested_start_date": self.requested_start_date.isoformat(),
            "requested_end_date": self.requested_end_date.isoformat(),
            "cities": list(self.cities),
            "expected_record_count": self.expected_record_count,
            "collected_record_count": self.collected_record_count,
            "excluded_date_count": self.excluded_date_count,
            "forecast_gap_sample_count": self.forecast_gap_sample_count,
            "excluded_record_count": self.excluded_record_count,
            "dataset_sha256": self.dataset_sha256,
            "exclusions": [item.to_mapping() for item in self.exclusions],
        }

    @property
    def report_sha256(self) -> str:
        return _sha256(_canonical_bytes(self.to_mapping()))

    def to_json(self) -> str:
        payload = dict(self.to_mapping())
        payload["report_sha256"] = self.report_sha256
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_unavailable_run_registry(path: Path) -> dict[datetime, UnavailableRunEvidence]:
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"invalid unavailable-run evidence: {path}") from exc
    if not isinstance(decoded, dict):
        raise CalibrationError("unavailable-run evidence must be an object")
    payload = cast(dict[str, object], decoded)
    if (
        payload.get("schema_version") != 1
        or payload.get("provider") != _EXPECTED_PROVIDER
        or payload.get("model") != _EXPECTED_MODEL
    ):
        raise CalibrationError("unavailable-run evidence contract mismatch")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise CalibrationError("unavailable-run evidence entries must be a list")

    result: dict[datetime, UnavailableRunEvidence] = {}
    for raw_entry in cast(list[object], entries):
        if not isinstance(raw_entry, dict):
            raise CalibrationError("unavailable-run evidence entry must be an object")
        entry = cast(dict[str, object], raw_entry)
        run_raw = entry.get("run_initialized_at_utc")
        body = entry.get("response_body")
        body_sha = entry.get("response_body_sha256")
        reason = entry.get("reason")
        source_url = entry.get("observed_request_url")
        workflow_run = entry.get("observed_in_workflow_run")
        if (
            not isinstance(run_raw, str)
            or not isinstance(body, str)
            or not isinstance(body_sha, str)
            or not isinstance(reason, str)
            or not isinstance(source_url, str)
            or isinstance(workflow_run, bool)
            or not isinstance(workflow_run, int)
            or entry.get("http_status") != 400
        ):
            raise CalibrationError("unavailable-run evidence entry fields are invalid")
        try:
            run = datetime.fromisoformat(run_raw).astimezone(UTC)
        except ValueError as exc:
            raise CalibrationError("unavailable-run evidence run time is invalid") from exc
        if _sha256(body.encode()) != body_sha:
            raise CalibrationError("unavailable-run evidence response hash mismatch")
        try:
            response: object = json.loads(body)
        except json.JSONDecodeError as exc:
            raise CalibrationError("unavailable-run provider response is invalid JSON") from exc
        if not isinstance(response, dict):
            raise CalibrationError("unavailable-run provider response must be an object")
        response_mapping = cast(dict[str, object], response)
        if response_mapping.get("error") is not True or response_mapping.get("reason") != reason:
            raise CalibrationError("unavailable-run provider response does not match evidence")
        if not reason.startswith(_UNAVAILABLE_REASON_PREFIX):
            raise CalibrationError("unavailable-run reason is outside the approved provider contract")
        if run in result:
            raise CalibrationError(f"duplicate unavailable-run evidence: {run.isoformat()}")
        result[run] = UnavailableRunEvidence(
            run_initialized_at_utc=run,
            source_url=source_url,
            response_body_sha256=body_sha,
            reason=reason,
            workflow_run=workflow_run,
        )
    return result


def _missing_horizons(
    target_date: date,
    unavailable_runs: dict[datetime, UnavailableRunEvidence],
) -> dict[int, UnavailableRunEvidence]:
    result: dict[int, UnavailableRunEvidence] = {}
    for horizon in _HORIZONS:
        decision_day = target_date - timedelta(days=horizon)
        run = calibration_run_for_market_day(decision_day)
        evidence = unavailable_runs.get(run)
        if evidence is not None:
            result[horizon] = evidence
    return result


def _collect_partial_day(
    *,
    target_date: date,
    market: CalibrationMarketSpec,
    cache: ImmutableHttpCache,
    parity_report: ArchiveParityReport,
    available_horizons: tuple[int, ...],
    now_utc: datetime | None,
) -> CalibrationDataset | None:
    if not available_horizons:
        return None
    now = datetime.now(UTC) if now_utc is None else now_utc.astimezone(UTC)
    local_today = now.astimezone(ZoneInfo(market.market_timezone)).date()
    if target_date > local_today - timedelta(days=2):
        raise CalibrationError(
            f"dataset end date {target_date} is too recent for finalized {market.city} history"
        )

    history_url = market.history_url(target_date)
    capture = cache.get(
        namespace=f"observations/{market.city}",
        key=target_date.isoformat(),
        requested_url=history_url,
        suffix=".html",
        headers=_wunderground_headers(),
    )
    parsed = parse_wunderground_daily_history_html(
        capture.payload,
        source_url=capture.final_url,
        retrieved_at_utc=capture.retrieved_at_utc,
        market_id=MarketId(f"calibration:{market.city}:{target_date.isoformat()}"),
        station_id=market.station_id,
        market_date=target_date,
        market_timezone=market.market_timezone,
    )
    observation = parsed.evidence

    pairs = []
    for horizon in available_horizons:
        decision_day = target_date - timedelta(days=horizon)
        run = calibration_run_for_market_day(decision_day)
        forecast_url = single_run_url(
            market.forecast_location,
            run_initialized_at_utc=run,
        )
        raw_capture = cache.get(
            namespace=f"forecasts/{market.city}",
            key=decision_day.isoformat(),
            requested_url=forecast_url,
            suffix=".json",
            headers={"User-Agent": "hermes-weatherbot-calibration/1"},
        )
        forecasts = parse_single_run_daily_highs(
            raw_capture.payload,
            source_url=raw_capture.final_url,
            location=market.forecast_location,
            market_day=decision_day,
            run_initialized_at_utc=run,
            retrieved_at_utc=raw_capture.retrieved_at_utc,
        )
        candidates = [
            item
            for item in forecasts.forecasts
            if item.forecast.market_date == target_date and item.lead_days == horizon
        ]
        if len(candidates) != 1:
            raise CalibrationError(
                f"expected one {market.city} D+{horizon} forecast for {target_date}, "
                f"found {len(candidates)}"
            )
        pairs.append((candidates[0], observation))

    return build_calibration_dataset(
        pairs,
        forecast_contract_id=parity_report.reference_contract_id,
        observation_contract_id=OBSERVATION_CONTRACT_ID,
        parity_reports=(parity_report,),
    )


def _observation_exclusion(
    *,
    market: CalibrationMarketSpec,
    target_date: date,
    cache: ImmutableHttpCache,
    error: WeatherUndergroundHistoryError,
) -> CalibrationSourceGap:
    history_url = market.history_url(target_date)
    frozen = cache.get(
        namespace=f"observations/{market.city}",
        key=target_date.isoformat(),
        requested_url=history_url,
        suffix=".html",
        headers={},
    )
    return CalibrationSourceGap(
        city=market.city,
        station_id=market.station_id,
        market_date=target_date,
        lead_days=None,
        source_class="observation",
        source_url=history_url,
        evidence_source_url=history_url,
        raw_payload_sha256=frozen.payload_sha256,
        reason_code="weather_underground_coverage",
        reason_detail=str(error),
    )


def _forecast_exclusion(
    *,
    market: CalibrationMarketSpec,
    target_date: date,
    lead_days: int,
    evidence: UnavailableRunEvidence,
) -> CalibrationSourceGap:
    decision_day = target_date - timedelta(days=lead_days)
    run = calibration_run_for_market_day(decision_day)
    requested_url = single_run_url(
        market.forecast_location,
        run_initialized_at_utc=run,
    )
    return CalibrationSourceGap(
        city=market.city,
        station_id=market.station_id,
        market_date=target_date,
        lead_days=lead_days,
        source_class="forecast",
        source_url=requested_url,
        evidence_source_url=evidence.source_url,
        raw_payload_sha256=evidence.response_body_sha256,
        reason_code="open_meteo_model_run_unavailable",
        reason_detail=evidence.reason,
        run_initialized_at_utc=evidence.run_initialized_at_utc,
    )


def collect_sparse_calibration_sweep(
    *,
    start_date: date,
    end_date: date,
    cache: ImmutableHttpCache,
    markets: tuple[CalibrationMarketSpec, ...],
    parity_report: ArchiveParityReport,
    unavailable_runs: dict[datetime, UnavailableRunEvidence],
    now_utc: datetime | None = None,
) -> tuple[CalibrationDataset, CalibrationSparseReport]:
    target_dates = _date_range(start_date, end_date)
    chunks: list[CalibrationDataset] = []
    exclusions: list[CalibrationSourceGap] = []

    for market in markets:
        for target_date in target_dates:
            missing = _missing_horizons(target_date, unavailable_runs)
            available = tuple(horizon for horizon in _HORIZONS if horizon not in missing)
            try:
                if not missing:
                    chunk = collect_calibration_dataset(
                        start_date=target_date,
                        end_date=target_date,
                        cache=cache,
                        markets=(market,),
                        parity_report=parity_report,
                        now_utc=now_utc,
                    )
                else:
                    chunk = _collect_partial_day(
                        target_date=target_date,
                        market=market,
                        cache=cache,
                        parity_report=parity_report,
                        available_horizons=available,
                        now_utc=now_utc,
                    )
            except WeatherUndergroundHistoryError as exc:
                if not _is_coverage_only_error(exc):
                    raise
                exclusions.append(
                    _observation_exclusion(
                        market=market,
                        target_date=target_date,
                        cache=cache,
                        error=exc,
                    )
                )
                continue

            if chunk is not None:
                if chunk.manifest.record_count != len(available):
                    raise CalibrationError(
                        f"expected {len(available)} records for {market.city} {target_date}, "
                        f"got {chunk.manifest.record_count}"
                    )
                chunks.append(chunk)
            exclusions.extend(
                _forecast_exclusion(
                    market=market,
                    target_date=target_date,
                    lead_days=horizon,
                    evidence=evidence,
                )
                for horizon, evidence in sorted(missing.items())
            )

    dataset = merge_calibration_datasets(chunks)
    report = CalibrationSparseReport(
        requested_start_date=start_date,
        requested_end_date=end_date,
        cities=tuple(market.city for market in markets),
        expected_record_count=len(target_dates) * len(markets) * len(_HORIZONS),
        collected_record_count=dataset.manifest.record_count,
        exclusions=tuple(
            sorted(
                exclusions,
                key=lambda item: (
                    item.market_date,
                    item.city,
                    -1 if item.lead_days is None else item.lead_days,
                    item.reason_code,
                ),
            )
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
    parser = argparse.ArgumentParser(description="Build issue #12 sparse calibration dataset v2")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--records-out", required=True, type=Path)
    parser.add_argument("--manifest-out", required=True, type=Path)
    parser.add_argument("--exclusions-out", required=True, type=Path)
    parser.add_argument("--parity-evidence", required=True, type=Path)
    parser.add_argument("--unavailable-runs-evidence", required=True, type=Path)
    parser.add_argument("--city", action="append", dest="cities")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--request-delay-seconds", type=float, default=0.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    parity_report = parity_report_from_evidence(args.parity_evidence)
    unavailable_runs = load_unavailable_run_registry(args.unavailable_runs_evidence)
    cache = ImmutableHttpCache(
        root=args.cache_dir,
        timeout_seconds=args.timeout_seconds,
        request_delay_seconds=args.request_delay_seconds,
        offline=args.offline,
    )
    dataset, report = collect_sparse_calibration_sweep(
        start_date=args.start_date,
        end_date=args.end_date,
        cache=cache,
        markets=_selected_markets(args.cities),
        parity_report=parity_report,
        unavailable_runs=unavailable_runs,
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
                "excluded_dates": report.excluded_date_count,
                "forecast_gap_samples": report.forecast_gap_sample_count,
                "excluded_records": report.excluded_record_count,
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
