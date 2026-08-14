"""Resumable historical dataset collection for forecast calibration.

The builder freezes every external response in an immutable local cache before parsing it.
A rerun therefore replays the exact same bytes and retrieval timestamp instead of silently
incorporating a later provider revision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time as time_module
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weatherbot.domain import MarketId, WeatherObservationEvidence
from weatherbot.forecasting.archive import (
    PRODUCTION_FORECAST_CONTRACT_ID,
    SINGLE_RUN_CAPTURE_CONTRACT_ID,
    CalibrationLocation,
    OpenMeteoSingleRunCapture,
    calibration_run_for_market_day,
    parse_single_run_daily_highs,
    single_run_url,
)
from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_data import (
    ArchiveParityPolicy,
    ArchiveParityReport,
    CalibrationDataset,
    ForecastCalibrationEvidence,
    build_calibration_dataset,
    write_calibration_dataset,
)
from weatherbot.forecasting.contracts import (
    CALIBRATION_LEAD_DAYS,
    OBSERVATION_CONTRACT_ID,
)
from weatherbot.resolution.wunderground import parse_wunderground_daily_history_html

DEFAULT_PARITY_POLICY = ArchiveParityPolicy(
    min_pairs=18,
    min_reference_coverage=1.0,
    max_mae_f=0.35,
    max_abs_error_f=0.5,
)
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_REQUEST_DELAY_SECONDS = 0.5
_DEFAULT_HORIZONS = CALIBRATION_LEAD_DAYS
_CACHE_SCHEMA_VERSION = 1


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


@dataclass(frozen=True, slots=True)
class CalibrationMarketSpec:
    city: str
    climate_region: str
    latitude: Decimal
    longitude: Decimal
    market_timezone: str
    station_id: str
    resolution_source_url: str

    def __post_init__(self) -> None:
        city = self.city.strip().lower()
        region = self.climate_region.strip().lower()
        station = self.station_id.strip().upper()
        source = self.resolution_source_url.strip().rstrip("/")
        if not city or not region or not station:
            raise CalibrationError("calibration market text fields must not be blank")
        latitude = _decimal(self.latitude, label="market latitude")
        longitude = _decimal(self.longitude, label="market longitude")
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise CalibrationError("market latitude is outside [-90, 90]")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise CalibrationError("market longitude is outside [-180, 180]")
        try:
            timezone = ZoneInfo(self.market_timezone.strip())
        except ZoneInfoNotFoundError as exc:
            raise CalibrationError(f"invalid market timezone: {self.market_timezone!r}") from exc
        if not source.startswith("https://www.wunderground.com/history/daily/"):
            raise CalibrationError(
                "resolution source must be a Weather Underground daily-history URL"
            )
        if not source.upper().endswith(f"/{station}"):
            raise CalibrationError("resolution source station differs from station_id")
        object.__setattr__(self, "city", city)
        object.__setattr__(self, "climate_region", region)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)
        object.__setattr__(self, "market_timezone", timezone.key)
        object.__setattr__(self, "station_id", station)
        object.__setattr__(self, "resolution_source_url", source)

    @property
    def forecast_location(self) -> CalibrationLocation:
        return CalibrationLocation(
            city=self.city,
            climate_region=self.climate_region,
            latitude=self.latitude,
            longitude=self.longitude,
            market_timezone=self.market_timezone,
        )

    def history_url(self, market_date: date) -> str:
        return f"{self.resolution_source_url}/date/{market_date.isoformat()}"


DEFAULT_MARKETS: tuple[CalibrationMarketSpec, ...] = (
    CalibrationMarketSpec(
        city="nyc",
        climate_region="northeast",
        latitude=Decimal("40.7772"),
        longitude=Decimal("-73.8726"),
        market_timezone="America/New_York",
        station_id="KLGA",
        resolution_source_url=(
            "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA"
        ),
    ),
    CalibrationMarketSpec(
        city="chicago",
        climate_region="ohio_valley",
        latitude=Decimal("41.9742"),
        longitude=Decimal("-87.9073"),
        market_timezone="America/Chicago",
        station_id="KORD",
        resolution_source_url=("https://www.wunderground.com/history/daily/us/il/chicago/KORD"),
    ),
    CalibrationMarketSpec(
        city="miami",
        climate_region="southeast",
        latitude=Decimal("25.7959"),
        longitude=Decimal("-80.2870"),
        market_timezone="America/New_York",
        station_id="KMIA",
        resolution_source_url=("https://www.wunderground.com/history/daily/us/fl/miami/KMIA"),
    ),
    CalibrationMarketSpec(
        city="dallas",
        climate_region="south",
        latitude=Decimal("32.8471"),
        longitude=Decimal("-96.8518"),
        market_timezone="America/Chicago",
        station_id="KDAL",
        resolution_source_url=("https://www.wunderground.com/history/daily/us/tx/dallas/KDAL"),
    ),
    CalibrationMarketSpec(
        city="seattle",
        climate_region="northwest",
        latitude=Decimal("47.4502"),
        longitude=Decimal("-122.3088"),
        market_timezone="America/Los_Angeles",
        station_id="KSEA",
        resolution_source_url=("https://www.wunderground.com/history/daily/us/wa/seatac/KSEA"),
    ),
    CalibrationMarketSpec(
        city="atlanta",
        climate_region="southeast",
        latitude=Decimal("33.6407"),
        longitude=Decimal("-84.4277"),
        market_timezone="America/New_York",
        station_id="KATL",
        resolution_source_url=("https://www.wunderground.com/history/daily/us/ga/atlanta/KATL"),
    ),
)


@dataclass(frozen=True, slots=True)
class CachedHttpCapture:
    requested_url: str
    final_url: str
    retrieved_at_utc: datetime
    payload_sha256: str
    payload: bytes

    def __post_init__(self) -> None:
        if self.retrieved_at_utc.tzinfo is None or self.retrieved_at_utc.utcoffset() is None:
            raise CalibrationError("cache retrieval time must be timezone-aware")
        digest = _sha256(self.payload_sha256, label="cache payload hash")
        actual = hashlib.sha256(self.payload).hexdigest()
        if actual != digest:
            raise CalibrationError("cached payload hash does not match payload bytes")
        object.__setattr__(self, "retrieved_at_utc", self.retrieved_at_utc.astimezone(UTC))
        object.__setattr__(self, "payload_sha256", digest)


@dataclass(slots=True)
class ImmutableHttpCache:
    root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    request_delay_seconds: float = _DEFAULT_REQUEST_DELAY_SECONDS
    offline: bool = False
    _last_network_request_at: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise CalibrationError("cache timeout must be finite and positive")
        if not math.isfinite(self.request_delay_seconds) or self.request_delay_seconds < 0:
            raise CalibrationError("request delay must be finite and non-negative")
        self.root = Path(self.root)

    def get(
        self,
        *,
        namespace: str,
        key: str,
        requested_url: str,
        suffix: str,
        headers: dict[str, str],
    ) -> CachedHttpCapture:
        safe_namespace = _safe_component(namespace, label="cache namespace")
        safe_key = _safe_component(key, label="cache key")
        payload_path = self.root / safe_namespace / f"{safe_key}{suffix}"
        metadata_path = payload_path.with_name(f"{payload_path.name}.meta.json")
        payload_exists = payload_path.exists()
        metadata_exists = metadata_path.exists()
        if payload_exists != metadata_exists:
            raise CalibrationError(
                f"partial cache entry exists for {namespace}/{key}; raw bytes and metadata are atomic"
            )
        if payload_exists:
            return self._load(payload_path, metadata_path, requested_url=requested_url)
        if self.offline:
            raise CalibrationError(f"offline cache miss for {namespace}/{key}")
        return self._fetch(
            payload_path,
            metadata_path,
            requested_url=requested_url,
            headers=headers,
        )

    def _load(
        self,
        payload_path: Path,
        metadata_path: Path,
        *,
        requested_url: str,
    ) -> CachedHttpCapture:
        payload = payload_path.read_bytes()
        try:
            decoded: object = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"invalid cache metadata: {metadata_path}") from exc
        if not isinstance(decoded, dict):
            raise CalibrationError("cache metadata must be a JSON object")
        metadata = cast(dict[str, object], decoded)
        if metadata.get("schema_version") != _CACHE_SCHEMA_VERSION:
            raise CalibrationError("unsupported cache metadata schema")
        cached_requested = _text(metadata.get("requested_url"), label="cache requested URL")
        if cached_requested != requested_url:
            raise CalibrationError("cache requested URL differs from the current request contract")
        final_url = _text(metadata.get("final_url"), label="cache final URL")
        retrieved = _timestamp(metadata.get("retrieved_at_utc"), label="cache retrieval time")
        digest = _sha256(
            _text(metadata.get("payload_sha256"), label="cache payload hash"),
            label="cache payload hash",
        )
        return CachedHttpCapture(
            requested_url=cached_requested,
            final_url=final_url,
            retrieved_at_utc=retrieved,
            payload_sha256=digest,
            payload=payload,
        )

    def _fetch(
        self,
        payload_path: Path,
        metadata_path: Path,
        *,
        requested_url: str,
        headers: dict[str, str],
    ) -> CachedHttpCapture:
        self._respect_delay()
        request = urllib.request.Request(requested_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
                final_url = response.geturl()
        except OSError as exc:
            raise CalibrationError(
                f"historical data request failed for {requested_url}: {exc}"
            ) from exc
        finally:
            self._last_network_request_at = time_module.monotonic()
        retrieved = datetime.now(UTC)
        digest = hashlib.sha256(payload).hexdigest()
        capture = CachedHttpCapture(
            requested_url=requested_url,
            final_url=final_url,
            retrieved_at_utc=retrieved,
            payload_sha256=digest,
            payload=payload,
        )
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(payload_path, payload)
        metadata = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "requested_url": requested_url,
            "final_url": final_url,
            "retrieved_at_utc": retrieved.isoformat(),
            "payload_sha256": digest,
        }
        _atomic_write_text(
            metadata_path,
            json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
        return capture

    def _respect_delay(self) -> None:
        previous = self._last_network_request_at
        if previous is None or self.request_delay_seconds <= 0:
            return
        remaining = self.request_delay_seconds - (time_module.monotonic() - previous)
        if remaining > 0:
            time_module.sleep(remaining)


def parity_report_from_evidence(
    evidence_path: str | Path,
    *,
    policy: ArchiveParityPolicy = DEFAULT_PARITY_POLICY,
) -> ArchiveParityReport:
    path = Path(evidence_path)
    try:
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"invalid archive parity evidence: {path}") from exc
    if not isinstance(decoded, dict):
        raise CalibrationError("archive parity evidence must be a JSON object")
    evidence = cast(dict[str, object], decoded)
    if evidence.get("schema_version") != 1:
        raise CalibrationError("unsupported archive parity evidence schema")
    reference_contract = _text(
        evidence.get("effective_production_contract_id"),
        label="parity production contract",
    )
    candidate_contract = _text(
        evidence.get("archive_capture_contract_id"),
        label="parity archive contract",
    )
    if reference_contract != PRODUCTION_FORECAST_CONTRACT_ID:
        raise CalibrationError("parity evidence targets a different production forecast contract")
    if candidate_contract != SINGLE_RUN_CAPTURE_CONTRACT_ID:
        raise CalibrationError("parity evidence targets a different archive capture contract")
    raw_rows = evidence.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise CalibrationError("archive parity evidence has no rows")
    rows = cast(list[object], raw_rows)
    errors: list[float] = []
    identities: list[str] = []
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            raise CalibrationError("archive parity evidence row must be an object")
        row = cast(dict[str, object], raw_row)
        city = _text(row.get("city"), label="parity city").lower()
        target_date = _date(row.get("target_date"), label="parity target date")
        horizon = _text(row.get("horizon"), label="parity horizon")
        if not horizon.startswith("D+"):
            raise CalibrationError("parity horizon must use D+n notation")
        try:
            lead_days = int(horizon[2:])
        except ValueError as exc:
            raise CalibrationError("parity horizon must contain an integer") from exc
        rounded_error = _number(row.get("rounded_error_f"), label="parity rounded error")
        if rounded_error != 0:
            raise CalibrationError("archive parity evidence contains a rounded mismatch")
        raw_error = float(
            _number(row.get("raw_error_from_rounded_reference_f"), label="parity raw error")
        )
        errors.append(raw_error)
        identities.append(f"{city}|{reference_contract}|{target_date.isoformat()}|D+{lead_days}")
    if len(identities) != len(set(identities)):
        raise CalibrationError("archive parity evidence contains duplicate identities")
    summary = evidence.get("summary")
    if not isinstance(summary, dict):
        raise CalibrationError("archive parity evidence summary must be an object")
    summary_mapping = cast(dict[str, object], summary)
    pairs = _integer(summary_mapping.get("pairs"), label="parity pair count")
    exact_matches = _integer(
        summary_mapping.get("rounded_exact_matches"),
        label="parity exact-match count",
    )
    if pairs != len(rows) or exact_matches != pairs:
        raise CalibrationError("archive parity evidence does not report complete rounded agreement")
    mean_error = sum(errors) / len(errors)
    mae = sum(abs(value) for value in errors) / len(errors)
    rmse = math.sqrt(sum(value * value for value in errors) / len(errors))
    max_abs = max(abs(value) for value in errors)
    reported_mae = float(
        _number(summary_mapping.get("mae_vs_rounded_reference_f"), label="reported parity MAE")
    )
    reported_max = float(
        _number(
            summary_mapping.get("max_abs_vs_rounded_reference_f"),
            label="reported parity maximum error",
        )
    )
    if not math.isclose(mae, reported_mae, rel_tol=0, abs_tol=1e-12):
        raise CalibrationError("archive parity MAE differs from committed evidence summary")
    if not math.isclose(max_abs, reported_max, rel_tol=0, abs_tol=1e-12):
        raise CalibrationError(
            "archive parity maximum error differs from committed evidence summary"
        )
    identity_sha = hashlib.sha256("\n".join(sorted(identities)).encode()).hexdigest()
    return ArchiveParityReport(
        reference_contract_id=reference_contract,
        candidate_contract_id=candidate_contract,
        policy=policy,
        reference_count=pairs,
        candidate_count=pairs,
        matched_count=pairs,
        reference_coverage=1.0,
        mean_error_f=mean_error,
        mae_f=mae,
        rmse_f=rmse,
        max_abs_error_f=max_abs,
        matched_identity_sha256=identity_sha,
    )


def collect_calibration_dataset(
    *,
    start_date: date,
    end_date: date,
    cache: ImmutableHttpCache,
    markets: tuple[CalibrationMarketSpec, ...] = DEFAULT_MARKETS,
    parity_report: ArchiveParityReport,
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    now_utc: datetime | None = None,
) -> CalibrationDataset:
    if end_date < start_date:
        raise CalibrationError("dataset end date must not precede start date")
    if not markets:
        raise CalibrationError("dataset requires at least one market specification")
    normalized_horizons = tuple(sorted(horizons))
    if normalized_horizons != _DEFAULT_HORIZONS:
        raise CalibrationError("issue #12 v1 dataset requires D+0, D+1, and D+2 horizons")
    now = datetime.now(UTC) if now_utc is None else _aware_utc(now_utc, label="build time")
    target_dates = _date_range(start_date, end_date)
    pairs: list[tuple[ForecastCalibrationEvidence, WeatherObservationEvidence]] = []

    for market in markets:
        local_today = now.astimezone(ZoneInfo(market.market_timezone)).date()
        if end_date > local_today - timedelta(days=2):
            raise CalibrationError(
                f"dataset end date {end_date} is too recent for finalized {market.city} history"
            )

        observations: dict[date, WeatherObservationEvidence] = {}
        for target_date in target_dates:
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
            observations[target_date] = parsed.evidence

        decision_days = sorted(
            {
                target_date - timedelta(days=horizon)
                for target_date in target_dates
                for horizon in horizons
            }
        )
        forecast_captures: dict[date, OpenMeteoSingleRunCapture] = {}
        for decision_day in decision_days:
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
            forecast_captures[decision_day] = parse_single_run_daily_highs(
                raw_capture.payload,
                source_url=raw_capture.final_url,
                location=market.forecast_location,
                market_day=decision_day,
                run_initialized_at_utc=run,
                retrieved_at_utc=raw_capture.retrieved_at_utc,
            )

        for target_date in target_dates:
            observation = observations[target_date]
            for horizon in horizons:
                decision_day = target_date - timedelta(days=horizon)
                candidates = [
                    item
                    for item in forecast_captures[decision_day].forecasts
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
        forecast_contract_id=PRODUCTION_FORECAST_CONTRACT_ID,
        observation_contract_id=OBSERVATION_CONTRACT_ID,
        parity_reports=(parity_report,),
    )


def _wunderground_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _number(value: object, *, label: str) -> Decimal:
    return _decimal(value, label=label)


def _integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalibrationError(f"{label} must be an integer")
    return value


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"{label} must be non-blank text")
    return value.strip()


def _sha256(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise CalibrationError(f"{label} must be a SHA-256 digest")
    return normalized


def _timestamp(value: object, *, label: str) -> datetime:
    text = _text(value, label=label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationError(f"{label} must be ISO-8601") from exc
    return _aware_utc(parsed, label=label)


def _date(value: object, *, label: str) -> date:
    text = _text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalibrationError(f"{label} must use YYYY-MM-DD") from exc


def _aware_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _safe_component(value: str, *, label: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise CalibrationError(f"{label} is not a safe relative cache component")
    if any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/."
        for character in normalized
    ):
        raise CalibrationError(f"{label} contains unsupported characters")
    return normalized


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise CalibrationError(f"stale cache temporary file exists: {temporary}")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise CalibrationError(f"stale cache temporary file exists: {temporary}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


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
        description="Build a reproducible forecast-calibration dataset"
    )
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/calibration/cache"))
    parser.add_argument(
        "--records-out",
        type=Path,
        default=Path("data/calibration/dataset.jsonl"),
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=Path("data/calibration/dataset-manifest.json"),
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
    dataset = collect_calibration_dataset(
        start_date=args.start_date,
        end_date=args.end_date,
        cache=cache,
        markets=_selected_markets(args.cities),
        parity_report=parity_report,
    )
    write_calibration_dataset(
        dataset,
        records_path=args.records_out,
        manifest_path=args.manifest_out,
    )
    print(
        json.dumps(
            {
                "records": dataset.manifest.record_count,
                "start_date": dataset.manifest.start_date.isoformat(),
                "end_date": dataset.manifest.end_date.isoformat(),
                "dataset_sha256": dataset.manifest.dataset_sha256,
                "manifest_sha256": dataset.manifest.manifest_sha256,
                "cache_dir": str(args.cache_dir),
                "offline": bool(args.offline),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
