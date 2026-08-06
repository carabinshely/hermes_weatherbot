"""CLI for immutable observed-temperature backfills and source revisions."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from weatherbot.domain import MarketId, WeatherObservationEvidence
from weatherbot.domain.observation import ObservationEvidenceStatus
from weatherbot.persistence import SQLiteEventStore
from weatherbot.resolution.observations import (
    ObservationRecorder,
    parse_optional_timestamp,
    payload_sha256,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record one immutable authoritative weather observation"
    )
    parser.add_argument("--database", default="state/ledger.sqlite3")
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--market-timezone", required=True)
    parser.add_argument("--temperature", required=True)
    parser.add_argument("--unit", choices=("C", "F"), required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--station-id", required=True)
    parser.add_argument("--measurement-basis", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--payload-file", required=True)
    parser.add_argument(
        "--status",
        choices=tuple(status.value for status in ObservationEvidenceStatus),
        default=ObservationEvidenceStatus.FINAL.value,
    )
    parser.add_argument("--source-timestamp")
    parser.add_argument("--supersedes-payload-hash")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload_path = Path(args.payload_file)
    if not payload_path.is_file():
        raise SystemExit(f"payload file does not exist: {payload_path}")
    evidence = WeatherObservationEvidence(
        market_id=MarketId(args.market_id),
        source_name=args.source_name,
        source_url=args.source_url,
        station_id=args.station_id,
        measurement_basis=args.measurement_basis,
        market_date=date.fromisoformat(args.market_date),
        market_timezone=args.market_timezone,
        temperature=Decimal(args.temperature),
        unit=args.unit,
        retrieved_at=datetime.now(UTC),
        source_timestamp=parse_optional_timestamp(args.source_timestamp),
        source_revision=args.source_revision,
        status=ObservationEvidenceStatus(args.status),
        payload_hash=payload_sha256(payload_path),
        supersedes_payload_hash=args.supersedes_payload_hash,
    )
    with SQLiteEventStore(args.database) as store:
        appended = ObservationRecorder(store).record(evidence)
    print(
        f"Observation {'recorded' if appended else 'already present'}: "
        f"market={evidence.market_id} date={evidence.market_date} "
        f"temperature={evidence.temperature}°{evidence.unit} "
        f"payload={evidence.payload_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
