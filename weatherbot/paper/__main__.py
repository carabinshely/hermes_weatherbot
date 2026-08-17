"""Internal PAPER Research and Development CLI entry point."""


def _main() -> int:
    from weatherbot.paper.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
