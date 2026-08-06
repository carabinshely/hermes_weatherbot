# Polymarket SDK boundary

The repository uses the official unified Python package `polymarket-client`.
The dependency is pinned to the published prerelease `0.1.0b21` because the SDK is
still beta and its public API may change between releases.

## Supported now

`weatherbot.polymarket.OfficialPolymarketReadClient` supports credential-free public
workflows:

- fetch one binary market by market ID, slug, or URL;
- list open binary markets;
- preserve market ID, condition ID, YES token ID, and NO token ID as distinct fields;
- fetch an order book by outcome token ID only;
- fetch public buy price, midpoint, and spread by outcome token ID;
- normalize SDK responses into immutable repository-owned models.

The adapter owns no wallet, private key, API credentials, or signing configuration.
Tests inject a fake public SDK client and make no external requests.

## Authenticated trading status

Authenticated order submission, cancellation, and private order listing remain disabled.
The legacy bot imports a repository-owned fail-closed bridge. Every authenticated method
raises `AuthenticatedTradingUnavailable` before secure-client construction, signing,
transaction creation, or network submission.

The published SDK creates authenticated clients with
`SecureClient.create(private_key=..., wallet=...)`. It classifies the requested wallet and
maps wallet classes to signature values as follows:

- `0`: `EOA`;
- `1`: `POLY_PROXY`;
- `2`: `GNOSIS_SAFE`;
- `3`: `DEPOSIT_WALLET`.

For non-EOA modes, repository configuration requires an explicit wallet address. For EOA,
a future secure adapter may derive the signer address from the private key and pass it as
the requested wallet. This explicit step matters because omitting `wallet` in the official
SDK defaults to the signer's Deposit Wallet, not to EOA.

After secure-client construction, a future adapter must compare the SDK-detected wallet type
with `AccountConfiguration.signature_type` and reject any mismatch before preparing or
posting an order. Recognition of these types does not mean funded-wallet operation is
enabled. Identifier mapping, order validation, idempotent submission, reconciliation, and
portfolio risk controls must be complete before this boundary can be opened.

## Migration notes

The archived `py-clob-client` package and all `py_clob_client` runtime imports are removed.
Do not reintroduce them, including for read-only calls. Public market and order-book work
belongs behind `weatherbot.polymarket`; domain and persistence modules must not import SDK
models.

Behavior changes from the archived client:

- public access uses `PublicClient()` with no credentials;
- authenticated access will use `SecureClient.create(...)`, not a host/chain/key constructor;
- `WALLET` represents the wallet address acted for, not a generic funder parameter;
- signature type is derived by the SDK from signer and wallet, then checked against config;
- market, condition, YES token, and NO token identifiers remain distinct;
- all authenticated legacy methods now fail closed.

When upgrading `polymarket-client`:

1. update the exact pin in `pyproject.toml`;
2. regenerate `uv.lock`;
3. run strict Pyright and the adapter contract tests;
4. inspect release notes for model, pagination, account, signature, and order changes;
5. keep normalized repository models stable or introduce an explicit migration.
