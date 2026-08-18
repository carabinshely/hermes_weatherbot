# Security policy

## Repository scope

This policy covers credentials, deployments, code, configuration, and operational data
used by this repository. Hermes' supported product is a non-executing signal producer with
optional one-way PIP publication and internal deterministic PAPER Research and Development
(R&D).

## Credential taxonomy

### Supported public producer

Normal `bot_v3.py scan/run/status` operation requires **no financial credential**: no
wallet private key, wallet address, signature type, exchange-write token, approval, or
funds-control secret.

### Internal PAPER

`python -m weatherbot.paper evaluate ...` requires no wallet/private key, approval,
transaction signing, or exchange-write credential. PAPER uses frozen/read-only evidence and
isolated simulation state.

### Optional PIP exporter

PIP publication uses a dedicated Ed25519 **application-identity signing key**. It has no
wallet, exchange-write, transaction-signing, or funds-control authority and must never be
reused as a wallet key.

The private producer key must be loaded from a restricted external file/secret store,
never committed, logged, serialized into the signal log/outbox, or sent to PIP.

### Quarantined historical execution

Legacy variables such as `PK`, `WALLET`, and `SIG_TYPE` may still be recognized by
quarantined historical code. They are **not supported Hermes product credentials** and are
not required by the public producer or PAPER engine.

Optional local/operator credentials such as Telegram or legacy observation-provider tokens
must likewise stay outside Git.

## Secret handling

All credentials must be loaded from environment variables or an external secret store.
Committed configuration must never contain tokens, private keys, chat IDs, wallet secrets,
or producer signing private keys.

Runtime diagnostics may report only whether a credential is configured. They must never
print, log, serialize, or include credential values in exception messages.

## Incident response

When a credential used by this repository is exposed:

1. stop processes using it;
2. remove it from deployment/local configuration;
3. revoke it at the issuing provider or producer-key registry;
4. create a replacement only when the integration remains required;
5. store the replacement outside Git;
6. confirm the old credential no longer authenticates;
7. run secret scans and review logs before redeployment.

Do not copy credential values into issues, pull requests, logs, tests, or documentation.

## Preventive controls

- Pull requests run current-tree Gitleaks scanning.
- CI verifies the scanner detects a generated temporary canary.
- Secret-shaped keys are prohibited in committed public configuration.
- `.env` and local credential files remain untracked.
- Logs and diagnostics redact credential values.
- CI separately guards public and PAPER import graphs from wallet/exchange-write code.
- PIP exporter dependencies and credentials remain separate from the minimal producer
  profile.

## Reporting

Report suspected vulnerabilities privately to the repository owner. Do not open a public
issue containing a credential or exploit payload.
