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
    assert SENSITIVE_CONFIG_KEYS.isdisjoint(config), (
        "secret-shaped keys must not exist in committed configuration"
    )


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


def test_legacy_bot_loads_notifications_from_environment_only() -> None:
    source = Path("bot_v3.py").read_text(encoding="utf-8")
    assert '_cfg.get("telegram_bot_token"' not in source
    assert '_cfg.get("telegram_chat_id"' not in source
    assert '_cfg.get("vc_key"' not in source
    assert 'os.getenv("TELEGRAM_BOT_TOKEN"' in source
    assert 'os.getenv("TELEGRAM_CHAT_ID"' in source


def test_unused_vc_key_is_removed_from_legacy_bots() -> None:
    for path in (Path("bot_v2.py"), Path("bot_v3.py")):
        assert "VC_KEY" not in path.read_text(encoding="utf-8")
