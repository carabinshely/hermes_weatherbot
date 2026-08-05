# Security policy

## Credentials

All credentials must be loaded from environment variables or an external secret store.
`config.json` is public configuration and must never contain tokens, private keys, chat IDs,
or provider credentials.

Supported legacy environment variables are:

- `PK`
- `WALLET`
- `SIG_TYPE`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Runtime diagnostics may report only whether a value is configured. They must never print,
log, serialize, or include the credential value in an exception message.

## Rotation procedure

1. Stop every process using the exposed credential.
2. Revoke the old credential at its provider.
3. Create a replacement only if the integration is still needed.
4. Store the replacement outside Git and deploy it through the environment or a secret store.
5. Confirm the old credential no longer authenticates.
6. Run the repository secret scans before redeployment.

For Telegram bot tokens, revoke and regenerate the token through Telegram's official bot
management interface. The historical `vc_key` is unused by the current code; its owner must
identify the issuing provider and revoke it there.

## Historical exposure inherited from upstream

This repository is a fork of `nicolastinkl/hermes_weatherbot`. The exposed values existed in
the upstream Git history before this fork was created. Rewriting this fork alone cannot remove
objects that remain reachable from the public upstream repository or GitHub fork network.
Credential revocation is therefore mandatory and provides the actual containment. Do not copy
or quote historical values in issues, pull requests, logs, or documentation.

## Reporting

Report suspected vulnerabilities privately to the repository owner. Do not open a public issue
that contains a credential or an exploit payload.
