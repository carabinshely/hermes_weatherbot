# Dependency profiles

Hermes' supported product surface is a non-executing signal producer with an optional
one-way Prediction Intelligence Platform (PIP) exporter and a deterministic internal PAPER
Research and Development (R&D) engine.

## Public producer and internal PAPER

```bash
uv sync --locked --no-dev
```

The minimal profile intentionally excludes wallet, Web3, financial signing, and
authenticated exchange-write dependencies. The public producer and internal PAPER runtime
must import and operate without those packages.

For development and tests:

```bash
uv sync --locked --all-groups
```

## Optional PIP exporter profile

PIP publication adds producer-identity cryptography/canonicalization only:

```bash
uv pip install -r requirements-pip-export.txt
```

The exporter signing key is a dedicated Ed25519 application identity. It has no wallet,
transaction, exchange-write, or funds-control authority. Installing exporter dependencies
must not pull in the quarantined financial/live dependency set.

## Quarantined historical live extra

The repository still retains an optional `live` dependency group because historical
execution code has not yet been physically deleted:

```bash
uv sync --locked --no-dev --extra live
```

This is **not a supported Hermes product mode** and is not a forward roadmap. The public
`bot_v3.py` CLI has no live command/mode selector, cancellation command, wallet status, or
transaction path. The public producer and supported PAPER runtime cannot import the
quarantined execution graph.

## Continuous Integration (CI)

CI verifies:

- the minimal producer/PAPER profile excludes wallet/live packages;
- PIP-disabled imports work before exporter dependencies are installed;
- the PIP exporter profile contains its required crypto/canonicalization packages without
  importing wallet/live packages;
- public and PAPER transitive non-execution guards pass;
- public, PAPER, and PIP CLI smoke tests match the documented surfaces.
