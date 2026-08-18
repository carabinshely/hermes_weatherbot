"""Operational CLI for the one-way PIP SignalEnvelope exporter."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from weatherbot.pip import (
    PipExportError,
    PipOutbox,
    deliver_dead_letter_once,
    deliver_once,
    load_exporter_config,
    reconcile_signal_log,
)
from weatherbot.producer.config import load_producer_policy

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _config():
    return load_exporter_config(REPOSITORY_ROOT)


def _signal_log_path() -> Path:
    policy = load_producer_policy(REPOSITORY_ROOT)
    path = policy.signal_log_path
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def status() -> int:
    config = _config()
    print("Hermes PIP exporter")
    print(f"  enabled: {str(config.enabled).lower()}")
    print(f"  endpoint: {config.endpoint or '(unset)'}")
    print(f"  outbox: {config.outbox_path}")
    print(f"  key_id: {config.key_id or '(unset)'}")
    if not config.outbox_path.exists():
        print("  outbox state: not initialized")
        return 0
    with PipOutbox(config.outbox_path) as outbox:
        summary = outbox.summary()
    print(f"  pending: {summary.pending}")
    print(f"  retry_wait: {summary.retry_wait}")
    print(f"  in_flight: {summary.in_flight}")
    print(f"  acknowledged: {summary.acknowledged}")
    print(f"  dead_letter: {summary.dead_letter}")
    print(
        "  oldest unacknowledged: "
        + (
            summary.oldest_unacknowledged_at.isoformat()
            if summary.oldest_unacknowledged_at is not None
            else "none"
        )
    )
    return 0


def reconcile() -> int:
    config = _config()
    if not config.enabled:
        print("PIP export disabled; reconciliation skipped", file=sys.stderr)
        return 0
    count = reconcile_signal_log(
        _signal_log_path(),
        config=config,
        repository_root=REPOSITORY_ROOT,
    )
    print(json.dumps({"reconciled_records": count}, sort_keys=True))
    return 0


def deliver_once_command() -> int:
    config = _config()
    if not config.enabled:
        print("PIP export disabled; delivery skipped", file=sys.stderr)
        return 0
    reconcile_signal_log(
        _signal_log_path(),
        config=config,
        repository_root=REPOSITORY_ROOT,
    )
    attempted = deliver_once(config=config)
    print(json.dumps({"attempted": attempted}, sort_keys=True))
    return 0


def retry_dead_letter(event_id: str, operator_id: str, reason: str) -> int:
    config = _config()
    if not config.enabled:
        print("ERROR: PIP export is disabled", file=sys.stderr)
        return 2
    attempted = deliver_dead_letter_once(
        config=config,
        event_id=event_id,
        operator_id=operator_id,
        reason=reason,
    )
    print(json.dumps({"attempted": attempted, "event_id": event_id}, sort_keys=True))
    return 0


def dead_letter(event_id: str, operator_id: str, reason: str) -> int:
    config = _config()
    if not config.outbox_path.exists():
        raise PipExportError("PIP outbox has not been initialized")
    with PipOutbox(config.outbox_path) as outbox:
        changed = outbox.operator_dead_letter(
            event_id=event_id,
            operator_id=operator_id,
            reason=reason,
        )
    if not changed:
        raise PipExportError("event is not pending or waiting for retry")
    print(json.dumps({"dead_lettered": True, "event_id": event_id}, sort_keys=True))
    return 0


def run_worker(interval_seconds: float) -> int:
    config = _config()
    if not config.enabled:
        print("ERROR: PIP export is disabled", file=sys.stderr)
        return 2
    if interval_seconds <= 0:
        print("ERROR: --interval-seconds must be positive", file=sys.stderr)
        return 2
    while True:
        reconcile_signal_log(
            _signal_log_path(),
            config=config,
            repository_root=REPOSITORY_ROOT,
        )
        attempted = deliver_once(config=config)
        if not attempted:
            time.sleep(interval_seconds)


def _operator_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], name: str):
    parser = subparsers.add_parser(name)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes signed PIP SignalEnvelope exporter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("reconcile")
    subparsers.add_parser("deliver-once")
    run = subparsers.add_parser("run")
    run.add_argument("--interval-seconds", type=float, default=5.0)
    _operator_parser(subparsers, "retry-dead-letter")
    _operator_parser(subparsers, "dead-letter")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return status()
        if args.command == "reconcile":
            return reconcile()
        if args.command == "deliver-once":
            return deliver_once_command()
        if args.command == "retry-dead-letter":
            return retry_dead_letter(args.event_id, args.operator, args.reason)
        if args.command == "dead-letter":
            return dead_letter(args.event_id, args.operator, args.reason)
        return run_worker(args.interval_seconds)
    except (PipExportError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
