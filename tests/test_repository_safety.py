from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

SENSITIVE_CONFIG_KEYS = {
    "telegram_bot_token",
    "telegram_chat_id",
    "vc_key",
}

FORBIDDEN_TRACKED_ROOTS = {"data", "state", "pip_tmp"}
REPRESENTATIVE_IGNORED_PATHS = (
    "data/state.json",
    "data/markets/sample.json",
    "data/learning/model.json",
    "data/calibration/v1/cache/sample.json",
    "data/calibration/v1/dataset.jsonl",
    "state/signals-v1.jsonl",
    "state/pip-outbox.sqlite3",
    "state/pip-dead-letter.sqlite3",
    "state/paper-ledger.sqlite3",
    "state/paper-archive/archive.sqlite3",
    "state/paper-experiments/paper_exp_test/summary.json",
    "state/dashboards/test.html",
    "state/runtime.log",
    "pip_tmp/pip.whl",
    "simulation.json",
    "sim_dashboard_test.html",
    "nested/.DS_Store",
)
REPRESENTATIVE_COMMITTABLE_PATHS = (
    "tests/fixtures/example.json",
    "evidence/paper/paper_exp_example/manifest.json",
    "evidence/calibration/example.json",
)
FORBIDDEN_EVIDENCE_NAMES = {".env"}
FORBIDDEN_EVIDENCE_SUFFIXES = {".pem", ".key"}
REQUIRED_PAPER_EVIDENCE_FILES = {
    "manifest.json",
    "provenance.json",
    "summary.json",
    "checksums.json",
}
REQUIRED_PAPER_PROVENANCE_KEYS = {
    "experiment_id",
    "strategy_id",
    "strategy_version",
    "policy_fingerprint",
    "source_commit",
    "manifest_sha256",
    "regeneration_command",
    "development_evidence_only",
}


def _tracked_files() -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return {item.decode() for item in completed.stdout.split(b"\0") if item}


def _is_ignored(path: str) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path],
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise AssertionError(f"git check-ignore failed for {path!r}: {completed.returncode}")
    return completed.returncode == 0


def _json_object(path: Path) -> dict[str, object]:
    decoded: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict), f"{path} must contain a JSON object"
    return decoded


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_committed_config_contains_no_secret_values() -> None:
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    producer = json.loads(Path("config/producer.json").read_text(encoding="utf-8"))
    assert SENSITIVE_CONFIG_KEYS.isdisjoint(config)
    assert SENSITIVE_CONFIG_KEYS.isdisjoint(producer)


def test_generated_and_machine_specific_artifacts_are_not_tracked() -> None:
    tracked = _tracked_files()
    assert ".env" not in tracked

    forbidden: list[str] = []
    for raw_path in sorted(tracked):
        path = Path(raw_path)
        if path.name == ".DS_Store":
            forbidden.append(raw_path)
            continue
        if path.parts and path.parts[0] in FORBIDDEN_TRACKED_ROOTS:
            forbidden.append(raw_path)
            continue
        if path.suffix == ".log" or ".sqlite" in path.name:
            forbidden.append(raw_path)
            continue
        if len(path.parts) == 1 and (
            (path.name.startswith("simulation") and path.suffix == ".json")
            or (path.name.startswith("sim_dashboard") and path.suffix == ".html")
        ):
            forbidden.append(raw_path)

    assert not forbidden, f"generated/runtime artifacts must not be tracked: {forbidden}"


def test_representative_runtime_outputs_are_ignored_by_git() -> None:
    unexpected = [path for path in REPRESENTATIVE_IGNORED_PATHS if not _is_ignored(path)]
    assert not unexpected, f"runtime/generated paths are not ignored: {unexpected}"


def test_fixture_and_evidence_namespaces_remain_committable() -> None:
    unexpected = [path for path in REPRESENTATIVE_COMMITTABLE_PATHS if _is_ignored(path)]
    assert not unexpected, f"intentional fixture/evidence paths are ignored: {unexpected}"


def test_fixture_and_evidence_trees_have_no_private_key_shaped_files() -> None:
    suspicious: list[str] = []
    for root in (Path("tests/fixtures"), Path("evidence")):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            lowered = path.name.casefold()
            if (
                lowered in FORBIDDEN_EVIDENCE_NAMES
                or path.suffix.casefold() in FORBIDDEN_EVIDENCE_SUFFIXES
                or "private-key" in lowered
                or lowered.startswith("wallet")
            ):
                suspicious.append(path.as_posix())
    assert not suspicious, f"private/local files are forbidden in fixtures/evidence: {suspicious}"


def test_promoted_paper_evidence_bundles_are_complete_and_checksums_match() -> None:
    root = Path("evidence/paper")
    if not root.exists():
        return

    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        present = {path.name for path in directory.iterdir() if path.is_file()}
        missing = REQUIRED_PAPER_EVIDENCE_FILES - present
        assert not missing, f"{directory} is missing required evidence files: {sorted(missing)}"

        summary = _json_object(directory / "summary.json")
        experiment_id = summary.get("experiment_id")
        assert experiment_id == directory.name
        assert summary.get("development_evidence_only") is True
        assert summary.get("verified_profitability") is False
        assert summary.get("public_or_paid_eligibility") is False
        assert isinstance(summary.get("strategy_id"), str) and summary["strategy_id"]
        assert isinstance(summary.get("strategy_version"), str) and summary["strategy_version"]
        assert isinstance(summary.get("policy_fingerprint"), str) and summary["policy_fingerprint"]

        provenance = _json_object(directory / "provenance.json")
        assert set(provenance) >= REQUIRED_PAPER_PROVENANCE_KEYS
        assert provenance["experiment_id"] == experiment_id
        assert provenance["strategy_id"] == summary["strategy_id"]
        assert provenance["strategy_version"] == summary["strategy_version"]
        assert provenance["policy_fingerprint"] == summary["policy_fingerprint"]
        assert provenance["development_evidence_only"] is True

        checksums = _json_object(directory / "checksums.json")
        assert checksums.get("summary.json") == _sha256(directory / "summary.json")
        if (directory / "evaluations.jsonl").exists():
            assert checksums.get("evaluations.jsonl") == _sha256(directory / "evaluations.jsonl")
        for name, expected in checksums.items():
            assert isinstance(name, str) and isinstance(expected, str)
            target = directory / name
            assert target.is_file(), f"checksum references missing file: {target}"
            assert _sha256(target) == expected, f"checksum mismatch: {target}"


def test_pull_request_target_is_limited_to_trusted_project_sync_workflow() -> None:
    users: list[str] = []
    for workflow in Path(".github/workflows").glob("*.yml"):
        content = workflow.read_text(encoding="utf-8")
        if "pull_request_target:" in content:
            users.append(workflow.name)
            assert workflow.name == "pip-project-sync.yml"
            assert "permissions: {}" in content
            assert "pull_request_target" in content
            assert "actions/checkout" not in content
    assert users == ["pip-project-sync.yml"]


def test_quarantined_legacy_credentials_remain_environment_only() -> None:
    v2_source = Path("bot_v2.py").read_text(encoding="utf-8")
    legacy_source = Path("bot_v3_legacy_impl.py").read_text(encoding="utf-8")
    public_source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert '_cfg.get("vc_key"' not in v2_source
    assert 'os.getenv("VC_KEY"' in v2_source
    assert "if not VC_KEY:" in v2_source

    for source in (legacy_source, public_source):
        assert '_cfg.get("telegram_bot_token"' not in source
        assert '_cfg.get("telegram_chat_id"' not in source
        assert '_cfg.get("vc_key"' not in source

    assert 'os.getenv("TELEGRAM_BOT_TOKEN"' in legacy_source
    assert 'os.getenv("TELEGRAM_CHAT_ID"' in legacy_source
    assert "PK" not in public_source
    assert "WALLET" not in public_source
