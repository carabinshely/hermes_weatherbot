# Polymarket SDK boundary

Hermes' supported product surface uses Polymarket only for **credential-free read-only
market evidence**. Authenticated trading is quarantined historical code, not a planned
product capability.

## Supported read-only use

`weatherbot.polymarket.OfficialPolymarketReadClient` normalizes public market and order-book
data into repository-owned models. It owns no wallet, private key, API credential, or
financial signing configuration.

Supported read-only workflows include:

- fetch/list public binary markets;
- preserve market ID, condition ID, semantic outcome and outcome-token ID distinctly;
- fetch selected-token order books and public price/spread evidence;
- normalize provider responses behind stable repository models.

The public producer uses this information only to form market identity and a read-only
executable-market reference for signal evaluation.

## Authenticated trading status

Historical authenticated submission/cancellation helpers remain fail-closed and
quarantined for compatibility/regression purposes. They are not a supported Hermes mode,
not a forward roadmap item, and must not be imported by the public producer or internal
PAPER engine.

The optional historical live dependency group therefore does not imply product support for
wallets, approvals, signing, orders, cancellation, redemption, or settlement execution.

## Upgrade discipline

When the read-only SDK dependency changes:

1. update and lock the exact dependency;
2. run strict type checking and adapter contract tests;
3. inspect upstream changes to public market/order-book models and pagination;
4. keep repository-owned normalized models stable or perform an explicit migration;
5. verify public and PAPER non-execution import guards still pass.
