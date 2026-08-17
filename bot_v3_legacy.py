#!/usr/bin/env python3
"""Quarantined compatibility facade for historical Hermes execution code.

The implementation is retained in ``bot_v3_legacy_impl.py`` only for historical
compatibility and regression evidence. Supported public and PAPER runtimes do not import
this facade, and it never exposes a supported LIVE command path.
"""

from __future__ import annotations

import sys

import bot_v3_legacy_impl as _impl
from bot_v3_legacy_impl import *  # noqa: F403

# UnsupportedTradingClient remains quarantined in bot_v3_legacy_impl.py.


def __getattr__(name: str):
    return getattr(_impl, name)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args and not args[0].startswith("-") else "scan"
    if command in {"scan", "run"}:
        print("ERROR: legacy strategy scanning is disabled; use bot_v3.py", file=sys.stderr)
        return 2
    if command == "cancel" or "--mode" in args and "live" in args:
        print("ERROR: LIVE execution is not a supported Hermes capability", file=sys.stderr)
        return 2
    return _impl.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
