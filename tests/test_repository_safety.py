from __future__ import annotations

import json
import subprocess
from pathlib import Path

SENSITIVE_CONFIG_KEYS = {
    "telegram_bot_token",
    "telegram_chat_id",
    "vc_key",
}


def test_committed_config_contains_no_secret_values() -> None:
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    producer = json.loads(Path("config/producer.json").read_text(encoding="utf-8"))
    assert SENSITIVE_CONFIG_KEYS.isdisjoint(config)
    assert SENSITIVE_CONFIG_KEYS.isdisjoint(producer)


def test_local_environment_file_is_not_tracked() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    tracked = {item.decode() for item in completed.stdout.split(b"\0") if item}
    assert ".env" not in tracked
    assert ".DS_Store" not in tracked


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


def test_public_operations_have_no_bespoke_daemon_helpers() -> None:
    assert not Path("start_bot_v3.sh").exists()
    assert not Path("stop_bot_v3.sh").exists()

    operational_sources = (
        Path("bot_v3.py"),
        Path("weatherbot/producer/cli.py"),
        Path("weatherbot/producer/__main__.py"),
        Path("weatherbot/pip/cli.py"),
        Path("weatherbot/runtime_control.py"),
    )
    forbidden = (
        "pgrep -f",
        "pkill -f",
        "nohup",
        "~/weatherbot",
        "/venv/bin/python",
        "unset ALL_PROXY",
        "unset HTTP_PROXY",
        "unset HTTPS_PROXY",
    )
    for path in operational_sources:
        content = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in content, f"{path} reintroduced host-specific operation: {marker}"
