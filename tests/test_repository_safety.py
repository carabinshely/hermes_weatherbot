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


def test_only_hardened_pip_sync_uses_pull_request_target() -> None:
    pip_sync_workflow = Path(".github/workflows/pip-project-sync.yml")

    for workflow in Path(".github/workflows").glob("*.yml"):
        content = workflow.read_text(encoding="utf-8")
        if workflow != pip_sync_workflow:
            assert "pull_request_target:" not in content
            continue

        assert "pull_request_target:" in content
        assert "permissions: {}" in content
        assert "uses:" not in content
        assert "actions/checkout" not in content
        assert "github.head_ref" not in content
        assert "github.event.pull_request.head" not in content


def test_legacy_bots_load_credentials_from_environment_only() -> None:
    v2_source = Path("bot_v2.py").read_text(encoding="utf-8")
    v3_source = Path("bot_v3.py").read_text(encoding="utf-8")

    assert '_cfg.get("vc_key"' not in v2_source
    assert 'os.getenv("VC_KEY"' in v2_source
    assert "if not VC_KEY:" in v2_source

    assert '_cfg.get("telegram_bot_token"' not in v3_source
    assert '_cfg.get("telegram_chat_id"' not in v3_source
    assert '_cfg.get("vc_key"' not in v3_source
    assert 'os.getenv("TELEGRAM_BOT_TOKEN"' in v3_source
    assert 'os.getenv("TELEGRAM_CHAT_ID"' in v3_source
    assert "VC_KEY" not in v3_source
