#!/usr/bin/env python3
"""Compatibility entrypoint for the public non-executing Hermes signal producer."""

from weatherbot.producer.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
