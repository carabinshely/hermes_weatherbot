"""Explicit internal PAPER research CLI, separate from the public producer surface."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from weatherbot.domain import MarketId, OutcomeId, RiskScope
from weatherbot.forecasting import CalibrationRuntimeError, load_calibrated_probability_runtime
from weatherbot.paper.config import PaperResearchConfig, load_paper_research_config
from weatherbot.paper.integration import (
    paper_runtime_status,
    recover_paper_runtime,
    reset_paper_runtime,
    submit_scanner_candidate,
)
from weatherbot.paper.service import PaperEntryStatus
from weatherbot.producer.config import load_producer_policy
from weatherbot.producer.market_source import fetch_token_order_book
from weatherbot.producer.scanner import collect_calibrated_candidates
from weatherbot.resolution import run_resolution_cycle

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_research_config() -> PaperResearchConfig:
    return load_paper_research_config(REPOSITORY_ROOT)


def scan_once(config: PaperResearchConfig | None = None) -> tuple[int, list[str]]:
    research = config or _load_research_config()
    runtime = research.runtime
    try:
        recover_paper_runtime(runtime=runtime)
    except Exception as exc:
        return 0, [f"PAPER recovery failed closed: {exc}"]
    try:
        calibration = load_calibrated_probability_runtime(repository_root=REPOSITORY_ROOT)
        policy = load_producer_policy(REPOSITORY_ROOT)
    except (CalibrationRuntimeError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return 0, [f"PAPER calibration/policy unavailable: {exc}"]

    candidates, errors = collect_calibrated_candidates(
        calibration_runtime=calibration,
        policy=policy,
    )
    simulated = 0
    for candidate in candidates:
        scope = RiskScope(
            market_id=MarketId(candidate.market_id),
            outcome_id=OutcomeId(candidate.token_id),
            event_id=candidate.event_id,
            city_key=candidate.city_slug,
            market_date=candidate.market_date,
        )
        try:
            result = submit_scanner_candidate(
                runtime=runtime,
                strategy_id=policy.strategy_id,
                calibrated=candidate.calibrated,
                scope=scope,
                weather=candidate.weather,
                event=candidate.event,
                decision_book=candidate.decision_book,
                condition_id=candidate.decision_book.condition_id,
                token_id=candidate.decision_book.token_id,
                freshness_policy=research.freshness_policy,
                cost_policy=research.cost_policy,
                fetch_book=fetch_token_order_book,
                audit_metadata={
                    "city_name": candidate.city_name,
                    "horizon": candidate.horizon,
                    "bucket_key": candidate.bucket.key,
                    "bucket_label": candidate.bucket.label,
                    "forecast_temperature_f": candidate.weather.signal_temperature_f,
                    "volume": candidate.volume,
                    "question": candidate.question,
                },
                owner_id=f"paper-scanner:{candidate.city_slug}:{candidate.market_date.isoformat()}",
            )
        except Exception as exc:
            errors.append(f"{candidate.city_name} {candidate.horizon}: PAPER failed: {exc}")
            continue
        if result.status in {PaperEntryStatus.FILLED, PaperEntryStatus.PARTIAL_FILL}:
            simulated += 1
    return simulated, errors


def show_status(config: PaperResearchConfig | None = None) -> int:
    research = config or _load_research_config()
    runtime = research.runtime
    status = paper_runtime_status(
        runtime=runtime,
        observed_at=datetime.now(UTC),
        freshness_policy=research.freshness_policy,
        cost_policy=research.cost_policy,
        fetch_book=fetch_token_order_book,
    )
    print("Hermes internal PAPER R&D")
    print(f"  ledger: {runtime.ledger_path}")
    print(f"  starting cash: {status.starting_cash.amount}")
    print(f"  available cash: {status.available_cash.amount}")
    print(f"  exposure: {status.exposure.amount}")
    print(f"  realized P/L: {status.realized_pnl.amount}")
    print(f"  unrealized P/L: {status.unrealized_pnl.amount}")
    print(f"  open positions: {status.open_positions}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal deterministic PAPER strategy R&D")
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=("scan", "run", "status", "resolve", "reset"),
    )
    parser.add_argument("--confirm-reset", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        research = _load_research_config()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: invalid PAPER research configuration: {exc}", file=sys.stderr)
        return 2

    if args.command == "status":
        return show_status(research)
    if args.command == "resolve":
        run_resolution_cycle(research.runtime.ledger_path)
        return 0
    if args.command == "reset":
        if not args.confirm_reset:
            print("ERROR: reset requires --confirm-reset", file=sys.stderr)
            return 2
        archive = reset_paper_runtime(runtime=research.runtime, reset_at=datetime.now(UTC))
        print(f"archived prior PAPER ledger to {archive}")
        return 0
    if args.command == "scan":
        _simulated, errors = scan_once(research)
        return 1 if errors else 0

    while True:
        scan_once(research)
        time.sleep(research.scan_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
