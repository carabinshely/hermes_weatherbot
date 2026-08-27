# Dependency profiles

Hermes' supported product surface is a non-executing signal producer.

## Public producer and internal PAPER

```bash
uv sync --locked --no-dev
```

This installs only the shared read-only runtime:

- `python-dotenv`
- `requests`

It intentionally excludes wallet, signing, Web3, and authenticated exchange dependencies.
The public producer, internal PAPER Research and Development (R&D), resolution tooling,
and observation tooling must all import in this profile.

For development and tests:

```bash
uv sync --locked --all-groups
```

## Quarantined historical live extra

The repository still retains an optional `live` dependency group because historical
execution code has not yet been physically deleted:

```bash
uv sync --locked --no-dev --extra live
```

It contains `eth-account`, `polymarket-client`, and `web3`.

This is **not a supported Hermes product mode**. The public `bot_v3.py` CLI has no LIVE
command, mode selector, cancellation command, wallet status, or transaction path. The
legacy facade rejects LIVE execution before delegating to the quarantined implementation.

The extra is retained only for historical compatibility and future cleanup; installing it
does not create a supported execution capability.

## Continuous Integration (CI)

Required Python jobs run the full suite in the minimal development profile and assert that
`eth_account`, `polymarket`, and `web3` are absent. CI also runs the public non-execution
import-graph guard, public CLI smoke tests, and internal PAPER tests.

The public product CI no longer installs or smoke-tests the historical live extra.
