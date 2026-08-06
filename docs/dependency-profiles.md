# Dependency profiles

The repository has two runtime installation profiles.

## Research and paper

```bash
uv sync --locked --no-dev
```

This installs only the shared public-data runtime:

- `python-dotenv`
- `requests`

It intentionally excludes wallet, signing, Web3, and the optional official Polymarket
SDK packages. Research, paper, resolution, observation backfill, and safe status commands
must import and run in this profile.

For development and tests without live packages:

```bash
uv sync --locked --all-groups
```

## Live-capable environment

```bash
uv sync --locked --no-dev --extra live
```

The `live` extra adds:

- `eth-account`
- `polymarket-client`
- `web3`

Installing the extra does not enable funded-wallet operation. Execution-mode gates,
credentials, account validation, and the remaining live-safety work still apply.

A live command in a minimal environment exits before credential, wallet, RPC, signing, or
SDK access and prints the exact installation command required to add the optional profile.

## Continuous integration

The required Python jobs first run the full suite in the minimal development profile and
assert that `eth_account`, `polymarket`, and `web3` are absent. Python 3.12 then installs
the `live` extra and smoke-tests its imports and Polymarket adapter tests. Security CI
audits the exported minimal and live runtime sets separately.
