from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

DEFAULT_CREDENTIAL_NAMES = (
    "PK",
    "WALLET",
    "SIG_TYPE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "VC_KEY",
)


def credential_status(
    names: Sequence[str] = DEFAULT_CREDENTIAL_NAMES,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return only configured/unset states; never return credential values."""
    source = os.environ if environ is None else environ
    return {name: "configured" if source.get(name, "").strip() else "unset" for name in names}


def credential_status_line(
    names: Sequence[str] = DEFAULT_CREDENTIAL_NAMES,
    environ: Mapping[str, str] | None = None,
) -> str:
    states = credential_status(names=names, environ=environ)
    return "Credentials: " + ", ".join(f"{name}={state}" for name, state in states.items())
