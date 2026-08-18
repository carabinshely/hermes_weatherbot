"""Command-line interface for the public non-executing Hermes producer."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from weatherbot.forecasting import (
    CalibratedProbabilityRuntime,
    CalibrationRuntimeError,
    load_calibrated_probability_runtime,
)
from weatherbot.pip import (
    PipExportError,
    load_exporter_config,
    promote_staged_signal,
    stage_signal,
)
from weatherbot.producer.config import ProducerPolicy, load_producer_policy
from weatherbot.producer.scanner import collect_calibrated_candidates
from weatherbot.producer.service import evaluate_candidate
from weatherbot.producer.store import append_signal

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_runtime() -> CalibratedProbabilityRuntime:
    return load_calibrated_probability_runtime(repository_root=REPOSITORY_ROOT)


def scan_once(policy: ProducerPolicy) -> tuple[int, list[str]]:
    try:
        calibration_runtime = _load_runtime()
    except CalibrationRuntimeError as exc:
        message = f"calibration unavailable; producer scan failed closed: {exc}"
        print(f"ERROR: {message}", file=sys.stderr)
        return 0, [message]

    candidates, errors = collect_calibrated_candidates(
        calibration_runtime=calibration_runtime,
        policy=policy,
    )
    pip_config = None
    try:
        pip_config = load_exporter_config(REPOSITORY_ROOT)
    except (PipExportError, OSError, ValueError) as exc:
        errors.append(f"PIP exporter configuration unavailable: {exc}")

    emitted = 0
    for candidate in candidates:
        signal, evaluation = evaluate_candidate(
            candidate,
            policy,
            evaluated_at=datetime.now(UTC),
        )
        if signal is None:
            reason = evaluation.rejection_reason
            reason_text = reason.value if reason is not None else "unknown"
            detail = evaluation.detail or "producer policy rejected candidate"
            errors.append(f"{candidate.city_name} {candidate.horizon}: {reason_text}: {detail}")
            continue

        # Freeze exact signed bytes before the independent JSONL durability boundary. The staged
        # intent is not deliverable; if staging fails, the Hermes decision still persists below.
        staged_for_pip = False
        if pip_config is not None and pip_config.enabled:
            try:
                staged_for_pip = stage_signal(
                    signal,
                    config=pip_config,
                    repository_root=REPOSITORY_ROOT,
                )
            except (PipExportError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
                errors.append(
                    f"{candidate.city_name} {candidate.horizon}: PIP staging failed: {exc}"
                )

        try:
            append_signal(policy.signal_log_path, signal)
        except (OSError, TypeError, ValueError) as exc:
            errors.append(
                f"{candidate.city_name} {candidate.horizon}: signal persistence failed: {exc}"
            )
            # The durable outcome of a failed write/fsync is ambiguous. Keep any staged frozen
            # bytes non-deliverable so restart reconciliation can match them against whatever
            # complete JSONL records actually survived; never guess by deleting recovery state.
            continue

        # The real Hermes decision is now immutable and durably recorded. Promotion is local
        # SQLite only; the producer process never opens a PIP network request.
        if staged_for_pip and pip_config is not None:
            try:
                if not promote_staged_signal(signal.signal_id, config=pip_config):
                    raise PipExportError("staged PIP intent missing after signal commit")
            except (PipExportError, OSError, sqlite3.Error, TypeError, ValueError) as exc:
                errors.append(
                    f"{candidate.city_name} {candidate.horizon}: PIP promotion failed: {exc}"
                )

        emitted += 1
        print(json.dumps(signal.to_mapping(), sort_keys=True, ensure_ascii=False))

    print(
        f"Producer scan: candidates={len(candidates)} emitted={emitted} errors={len(errors)}",
        file=sys.stderr,
    )
    return emitted, errors


def show_status(policy: ProducerPolicy) -> int:
    print("Hermes public producer")
    print(f"  strategy: {policy.strategy_id}@{policy.strategy_version}")
    print(f"  policy fingerprint: {policy.fingerprint}")
    print(f"  reference notional: ${policy.market_reference_notional}")
    print(f"  signal log: {policy.signal_log_path}")
    try:
        pip_config = load_exporter_config(REPOSITORY_ROOT)
        print(f"  PIP export: {'enabled' if pip_config.enabled else 'disabled'}")
    except (PipExportError, OSError, ValueError) as exc:
        print(f"  PIP export: configuration error ({exc})")
    try:
        runtime = _load_runtime()
    except CalibrationRuntimeError as exc:
        print(f"  calibration: unavailable ({exc})")
        return 0
    print(f"  calibration: available ({runtime.model.artifact.model_version})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermes non-executing calibrated weather-market signal producer"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=("scan", "run", "status"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = load_producer_policy(REPOSITORY_ROOT)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: invalid producer policy: {exc}", file=sys.stderr)
        return 2

    if args.command == "status":
        return show_status(policy)
    if args.command == "scan":
        _emitted, errors = scan_once(policy)
        return 1 if errors else 0

    while True:
        scan_once(policy)
        time.sleep(policy.scan_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
