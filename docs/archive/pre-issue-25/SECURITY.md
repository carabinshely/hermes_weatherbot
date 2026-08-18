# Security policy

## Repository scope

This policy covers credentials, deployments, code, configuration, and operational data used by this repository. Security conditions in other repositories are outside this project's implementation and release scope.

## Credentials

All credentials must be loaded from environment variables or an external secret store. `config.json` is public configuration and must never contain tokens, private keys, chat IDs, or provider credentials.

Supported legacy environment variables are:

- `PK`
- `WALLET`
- `SIG_TYPE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `VC_KEY`

Runtime diagnostics may report only whether a value is configured. They must never print, log, serialize, or include the credential value in an exception message.

## Incident response

When a credential used by this repository is exposed:

1. Stop every process using it.
2. Remove it from deployment environments and local configuration.
3. Revoke it at the issuing provider.
4. Create a replacement only when the integration is still required.
5. Store the replacement outside Git.
6. Confirm that the old credential no longer authenticates.
7. Run repository secret scans and review logs before redeployment.

Do not copy or quote credential values in issues, pull requests, logs, tests, or documentation.

## Preventive controls

- Pull requests must pass the current-tree Gitleaks scan.
- CI verifies that the scanner detects a generated temporary canary.
- Secret-shaped keys are prohibited in committed public configuration.
- `.env` and other local credential files must remain untracked.
- Logs and diagnostics must redact credential values.

## Reporting

Report suspected vulnerabilities privately to the repository owner. Do not open a public issue containing a credential or exploit payload.
