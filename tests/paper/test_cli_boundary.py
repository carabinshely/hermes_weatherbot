from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.producer.test_boundary import candidate, policy
from tests.quoting.helpers import NOW
from weatherbot.paper import cli as paper_cli
from weatherbot.paper.experiment import PaperEvidenceCase, PaperExperimentSpec


def _spec(*, strategy_version: str = "candidate-v1") -> PaperExperimentSpec:
    return PaperExperimentSpec(
        policy=replace(policy(), strategy_version=strategy_version),
        evidence_cases=(
            PaperEvidenceCase(
                case_id="cli-fixture",
                decision_at=NOW,
                candidate=candidate(),
            ),
        ),
    )


def test_internal_parser_exposes_evaluate_only() -> None:
    parser = paper_cli.build_parser()
    args = parser.parse_args(
        [
            "evaluate",
            "--manifest",
            "experiment.json",
            "--output",
            "state/paper-experiments",
        ]
    )

    assert args.command == "evaluate"
    assert args.manifest == Path("experiment.json")
    assert args.output == Path("state/paper-experiments")
    with pytest.raises(SystemExit):
        parser.parse_args(["scan"])
    with pytest.raises(SystemExit):
        parser.parse_args(["status"])


def test_manifest_is_minimal_and_fail_closed(tmp_path: Path) -> None:
    manifest = tmp_path / "experiment.json"
    manifest.write_text(
        json.dumps(
            {
                "factory": "weatherbot.paper.experiments.fixture:build",
                "arguments": {"strategy_version": "candidate-v2"},
            }
        ),
        encoding="utf-8",
    )

    factory, arguments = paper_cli._load_manifest(manifest)
    assert factory == "weatherbot.paper.experiments.fixture:build"
    assert arguments == {"strategy_version": "candidate-v2"}

    manifest.write_text(
        json.dumps(
            {
                "factory": "weatherbot.paper.experiments.fixture:build",
                "arguments": {},
                "publish": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported fields"):
        paper_cli._load_manifest(manifest)


def test_factory_loading_is_restricted_to_reviewed_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="must live under"):
        paper_cli._factory("weatherbot.producer.service:evaluate_candidate")
    with pytest.raises(ValueError, match="module-level"):
        paper_cli._factory("weatherbot.paper.experiments.fixture:factory.method")

    module = SimpleNamespace(
        build=lambda strategy_version: _spec(strategy_version=strategy_version)
    )
    monkeypatch.setattr(paper_cli.importlib, "import_module", lambda _name: module)
    built = paper_cli._build(
        "weatherbot.paper.experiments.fixture:build",
        {"strategy_version": "candidate-v3"},
    )

    assert built.strategy_version == "candidate-v3"


def test_build_rejects_non_spec_factory_result(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace(build=lambda: object())
    monkeypatch.setattr(paper_cli.importlib, "import_module", lambda _name: module)

    with pytest.raises(ValueError, match="return PaperExperimentSpec"):
        paper_cli._build("weatherbot.paper.experiments.fixture:build", {})


def test_cli_evaluates_repository_owned_frozen_experiment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "experiment.json"
    output = tmp_path / "results"
    manifest.write_text(
        json.dumps(
            {
                "factory": "weatherbot.paper.experiments.fixture:build",
                "arguments": {"strategy_version": "candidate-v4"},
            }
        ),
        encoding="utf-8",
    )
    module = SimpleNamespace(
        build=lambda strategy_version: _spec(strategy_version=strategy_version)
    )
    monkeypatch.setattr(paper_cli.importlib, "import_module", lambda _name: module)
    monkeypatch.setenv("PK", "must-not-matter")
    monkeypatch.setenv("WALLET", "must-not-matter")

    assert (
        paper_cli.main(
            [
                "evaluate",
                "--manifest",
                str(manifest),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    stdout = capsys.readouterr().out.splitlines()
    assert stdout[0].startswith("paper_exp_")
    result_directory = Path(stdout[1])
    assert result_directory.parent == output
    assert (result_directory / "summary.json").exists()
    assert (result_directory / "evaluations.jsonl").exists()
    assert (result_directory / "checksums.json").exists()


def test_cli_reports_invalid_manifest_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "invalid.json"
    manifest.write_text("{", encoding="utf-8")

    assert (
        paper_cli.main(
            [
                "evaluate",
                "--manifest",
                str(manifest),
                "--output",
                str(tmp_path / "results"),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "PAPER experiment failed closed" in error
    assert "Traceback" not in error
