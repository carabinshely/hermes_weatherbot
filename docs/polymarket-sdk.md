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

Account/signature configuration validation recognizes:

- `0`: EOA, with no separate funder;
- `1`: Polymarket proxy, requiring a funder address;
- `2`: Gnosis Safe, requiring a funder address;
- `3`: POLY-1271/embedded account, requiring a funder address.

Recognition does not mean funded-wallet operation is enabled. A future secure adapter must
also complete identifier mapping, order validation, idempotent submission, reconciliation,
and portfolio risk controls before this boundary can be opened.

## Migration notes

The archived `py-clob-client` package and all `py_clob_client` imports are removed. Do not
reintroduce them, including for read-only calls. Public market and order-book work belongs
behind `weatherbot.polymarket`; domain and persistence modules must not import SDK models.

When upgrading `polymarket-client`:

1. update the exact pin in `pyproject.toml`;
2. regenerate `uv.lock`;
3. run strict Pyright and the adapter contract tests;
4. inspect release notes for model, pagination, account, signature, and order changes;
5. keep normalized repository models stable or introduce an explicit migration.
