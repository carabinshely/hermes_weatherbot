"""Internal command-line entrypoint for deterministic PAPER experiments."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from weatherbot.paper.experiment import (
    PaperExperimentEngine,
    PaperExperimentSpec,
    StrategyEvaluator,
)
from weatherbot.paper.io import write_experiment_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m weatherbot.paper",
        description="Run internal deterministic PAPER strategy experiments.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    evaluate = subcommands.add_parser(
        "evaluate",
        help="evaluate one frozen experiment manifest",
    )
    evaluate.add_argument("--manifest", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    return parser


def _load_manifest(path: Path) -> tuple[str, Mapping[str, object]]:
    decoded: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("PAPER experiment manifest must be a JSON object")
    raw = cast(dict[str, object], decoded)
    unknown = set(raw) - {"factory", "arguments"}
    if unknown:
        raise ValueError(f"PAPER experiment manifest has unsupported fields: {sorted(unknown)}")
    factory = raw.get("factory")
    if not isinstance(factory, str) or not factory.strip() or ":" not in factory:
        raise ValueError("PAPER manifest factory must be 'module:function'")
    arguments = raw.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("PAPER manifest arguments must be an object")
    return factory.strip(), cast(Mapping[str, object], arguments)


def _factory(path: str) -> Callable[..., object]:
    module_name, function_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise ValueError(f"PAPER experiment factory is not callable: {path}")
    return cast(Callable[..., object], function)


def _build(
    factory_path: str,
    arguments: Mapping[str, object],
) -> tuple[PaperExperimentSpec, StrategyEvaluator]:
    built = _factory(factory_path)(**dict(arguments))
    if not isinstance(built, tuple) or len(built) != 2:
        raise ValueError(
            "PAPER experiment factory must return (PaperExperimentSpec, StrategyEvaluator)"
        )
    spec, evaluator = built
    if not isinstance(spec, PaperExperimentSpec):
        raise ValueError("PAPER experiment factory returned an invalid spec")
    if not callable(evaluator):
        raise ValueError("PAPER experiment factory returned a non-callable strategy evaluator")
    return spec, cast(StrategyEvaluator, evaluator)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "evaluate":
        raise AssertionError(f"unsupported PAPER command: {args.command}")
    factory_path, arguments = _load_manifest(args.manifest)
    spec, evaluator = _build(factory_path, arguments)
    result = PaperExperimentEngine().evaluate(spec, strategy_evaluator=evaluator)
    artifacts = write_experiment_artifacts(result, output_directory=args.output)
    print(result.experiment_id)
    print(artifacts.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
