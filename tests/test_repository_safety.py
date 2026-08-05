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
    for key in SENSITIVE_CONFIG_KEYS:
        assert not config.get(key), f"{key} must not contain a committed value"


def test_local_environment_file_is_not_tracked() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    tracked = {item.decode() for item in completed.stdout.split(b"\0") if item}
    assert ".env" not in tracked
    assert ".DS_Store" not in tracked


def test_workflows_do_not_use_pull_request_target() -> None:
    for workflow in Path(".github/workflows").glob("*.yml"):
        content = workflow.read_text(encoding="utf-8")
        assert "pull_request_target:" not in content
