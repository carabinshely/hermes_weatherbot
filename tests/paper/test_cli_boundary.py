from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import ModuleType

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


def _build_spec(strategy_version: str) -> PaperExperimentSpec:
    return _spec(strategy_version=strategy_version)


def _invalid_factory() -> object:
    return object()


def _module_with_factory(name: str, factory: object) -> ModuleType:
    module = ModuleType(name)
    setattr(module, "build", factory)
    return module


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


def test_manifest_rejects_unknown_fields_through_supported_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "experiment.json"
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
    assert "unsupported fields" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (
            "weatherbot.producer.service:evaluate_candidate",
            "must live under weatherbot.paper.experiments",
        ),
        (
            "weatherbot.paper.experiments.fixture:factory.method",
            "must name one module-level function",
        ),
    ],
)
def test_factory_loading_is_restricted_to_reviewed_namespace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    factory: str,
    expected: str,
) -> None:
    manifest = tmp_path / "experiment.json"
    manifest.write_text(
        json.dumps({"factory": factory, "arguments": {}}),
        encoding="utf-8",
    )

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
    assert expected in capsys.readouterr().err


def test_factory_result_must_be_experiment_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "experiment.json"
    manifest.write_text(
        json.dumps(
            {
                "factory": "weatherbot.paper.experiments.fixture:build",
                "arguments": {},
            }
        ),
        encoding="utf-8",
    )
    module = _module_with_factory(
        "weatherbot.paper.experiments.fixture",
        _invalid_factory,
    )

    def import_module(_name: str) -> ModuleType:
        return module

    monkeypatch.setattr(paper_cli.importlib, "import_module", import_module)

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
    assert "must return PaperExperimentSpec" in capsys.readouterr().err


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
    module = _module_with_factory(
        "weatherbot.paper.experiments.fixture",
        _build_spec,
    )

    def import_module(_name: str) -> ModuleType:
        return module

    monkeypatch.setattr(paper_cli.importlib, "import_module", import_module)
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
