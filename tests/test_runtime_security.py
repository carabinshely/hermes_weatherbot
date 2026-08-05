from __future__ import annotations

from runtime_security import credential_status, credential_status_line


def test_credential_status_never_returns_values() -> None:
    environment = {
        "PK": "super-secret-private-key",
        "WALLET": "0x1234",
        "TELEGRAM_BOT_TOKEN": "123456:secret-token",
        "VC_KEY": "visual-crossing-secret",
    }
    states = credential_status(environ=environment)
    assert states["PK"] == "configured"
    assert states["WALLET"] == "configured"
    assert states["TELEGRAM_BOT_TOKEN"] == "configured"
    assert states["TELEGRAM_CHAT_ID"] == "unset"
    assert states["VC_KEY"] == "configured"
    rendered = credential_status_line(environ=environment)
    for value in environment.values():
        assert value not in rendered


def test_credential_status_treats_whitespace_as_unset() -> None:
    states = credential_status(names=("TOKEN",), environ={"TOKEN": "   "})
    assert states == {"TOKEN": "unset"}
