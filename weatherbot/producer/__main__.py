"""Module entrypoint for the public non-executing Hermes signal producer."""


def _main() -> int:
    from weatherbot.producer.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
