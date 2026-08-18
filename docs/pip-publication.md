# PIP publication

Hermes publishes only already-made public `HermesSignal v1` decisions to Prediction Intelligence Platform (PIP). PIP delivery is downstream infrastructure and cannot change forecast probability, strategy policy, signal identity, PAPER experiments, sizing, risk, or execution behavior.

## Runtime boundary

```text
weather/market/calibration evidence
        -> evaluate_candidate()
        -> immutable HermesSignal
        -> build/sign/freeze SignalEnvelope
        -> durable non-deliverable SQLite staging intent
        -> fsync Hermes signal JSONL
        -> authoritative JSONL reconciliation / staging promotion
        -> durable SQLite outbox
        ---------------- async boundary ----------------
        -> python -m weatherbot.pip run
        -> POST /v1/events
        -> accepted / already_accepted / retry / rejected
```

The staging intent exists only to preserve exact signed bytes across the crash window between signal persistence and outbox promotion. It is never eligible for network delivery. If Hermes crashes after JSONL fsync but before promotion, reconciliation promotes the original frozen bytes instead of rebuilding or re-signing them with a newer key.

The producer process never waits for PIP HTTP delivery. Exporter configuration is not part of `ProducerPolicy` and therefore cannot change `policy_fingerprint`, `signal_id`, calibrated probabilities, or producer acceptance decisions.

## Contract and lifecycle identity

Issue #54 exports one PIP lifecycle event type: `signal.created`.

- `HermesSignal.condition_id` becomes PIP `market.market_id`.
- `HermesSignal.token_id` becomes PIP `market.outcome_id`.
- `model_probability` becomes `forecast.probability`.
- `market_reference.all_in_reference_price` becomes the executable market reference value.
- `market_reference.probability_edge` becomes decision edge.
- `evidence.mode` is `live`, meaning contemporaneous real producer evidence; it does **not** mean live trading or proof of execution.
- weather/calibration/source details stay under `hermes_weatherbot:weather:v1`.
- producer public/paid eligibility is explicitly `not_asserted` in the extension. PIP owns downstream eligibility decisions.

PIP lifecycle v1 permits exactly one `signal.created` event for a canonical `(producer_id, signal_id)`. Hermes can encounter the same logical `signal_id` in repeated scans, so the append-only signal log defines the PIP creation assertion: **the first complete durable JSONL occurrence of that `signal_id` is canonical**. Later occurrences do not create another PIP lifecycle event.

A staged intent is promotable only when its canonical event bytes match that first durable occurrence. If a pre-commit intent has no corresponding complete durable signal record, reconciliation retires it as an orphan. If the outbox already owns the signal, any leftover staging row is also retired.

Only the top-level `event` object is canonicalized with RFC 8785 JCS (JSON Canonicalization Scheme) and signed. The detached signature is not part of `event_sha256`.

## Market-reference evidence

Polymarket's provider order-book `hash` is retained as source provenance under the Hermes extension, but Hermes does not assume that provider-defined value is itself SHA-256.

PIP requires an immutable SHA-256 evidence digest. Hermes therefore computes the digest over a stable producer-owned `hermes.executable-market-reference.v1` JSON projection containing the exact condition/token identity, observation time, executable quote economics, provider order-book hash, and quote fingerprint used for the decision. The executable PIP market reference points to that evidence record.

## Signing key

The required key is a dedicated Ed25519 application-identity key. It must have no wallet, exchange-write, transaction-signing, or funds-control authority.

Install the exporter dependencies separately from the minimal and historical live profiles:

```bash
uv pip install -r requirements-pip-export.txt
```

The exporter dependencies are intentionally not required by the minimal producer profile. CI proves that `bot_v3`, the producer CLI, and the disabled PIP CLI import successfully before `cryptography` and `rfc8785` are installed.

Provide the raw 32-byte private key as canonical unpadded base64url in a restricted file. On POSIX systems Hermes rejects a signing-key file readable by group or world.

Required environment when enabled:

```text
HERMES_PIP_EXPORT_ENABLED=true
HERMES_PIP_ENDPOINT=https://<pip-host>/v1/events
HERMES_PIP_OUTBOX_PATH=state/pip-outbox.sqlite3
HERMES_PIP_SIGNING_KEY_PATH=/restricted/path/hermes-pip-ed25519.key
HERMES_PIP_KEY_ID=<immutable registered key id>
```

Never commit the private key, log it, put it in the outbox, reuse a wallet key, or send it to PIP. PIP receives only the registered public key through its separate operator process.

## Producer release provenance

`config/producer-releases/<strategy_version>.json` binds each immutable strategy version to the Hermes repository, the exact lowercase 40-hex Git commit SHA, and the decision-code identity. Mutable aliases such as `main` are rejected. Strategy-version release lookup also rejects path-like values.

The manifest's raw bytes are SHA-256 hashed and carried as the code artifact digest. Recovery of an old signal therefore never substitutes the current checkout revision. Missing or mismatched release provenance fails PIP publication closed without altering the Hermes signal.

## Outbox semantics

The PIP outbox is separate from the historical financial/event ledger.

SQLite uses WAL (Write-Ahead Logging), `synchronous=FULL`, and atomic `BEGIN IMMEDIATE` claims. Frozen identity columns are protected by a database trigger.

States:

```text
pending -> in_flight -> acknowledged
                    \-> retry_wait -> in_flight
                    \-> dead_letter
```

Automatic claims use a unique fencing token and a maximum 60-second lease. A claim is selected only when the entire proposed lease fits before the event's seven-day delivery horizon; near-horizon rows cannot block later eligible work. An expired automatic lease is recovered as an ambiguous retry while still inside the horizon. At horizon expiry, undelivered items remain retained as `dead_letter`; they are never silently deleted or acknowledged.

Local enqueue is idempotent:

```text
same (producer_id,event_id) + same digest/bytes -> existing item
same (producer_id,event_id) + different bytes   -> fail closed
```

Retries always resend the exact persisted `envelope_bytes`; they never rebuild or re-sign the event. This is required for safe recovery when PIP accepted a request but Hermes crashed before persisting the acknowledgement.

The frozen PIP producer-delivery retry policy is:

- base delay: 1 second;
- maximum delay: 60 seconds;
- exponential cap: 10;
- jitter: 0.5–1.0 of the capped raw delay;
- maximum delivery horizon: seven days from `event.generated_at`.

Attempt count is never a terminal criterion.

## HTTP result rules

Production endpoints must use HTTPS. HTTP is accepted only for localhost development. Redirects are never followed.

A response acknowledges an item only when the body is a valid bound `pip.event-delivery-result` v1 with matching `producer_id`, `event_id`, and `event_sha256`, and disposition `accepted` or `already_accepted` with a receipt ID.

A bare `2xx`, `202`, malformed body, mismatched binding, unknown result, timeout, redirect, `429`, or `5xx` is not acknowledgement. Ambiguous outcomes remain retryable. Only a valid bound `rejected` result becomes a permanent dead letter.

Decoded response bodies are bounded to 65,536 bytes before JSON interpretation. Completion uses the actual post-request time and is fenced by the still-valid claim token/lease, so a worker cannot persist a late result after its lease expires.

## Operations

```bash
python -m weatherbot.pip status
python -m weatherbot.pip reconcile
python -m weatherbot.pip deliver-once
python -m weatherbot.pip run
python -m weatherbot.pip retry-dead-letter \
  --event-id <event-id> --operator <operator-id> --reason <reason>
python -m weatherbot.pip dead-letter \
  --event-id <event-id> --operator <operator-id> --reason <reason>
```

`reconcile` scans complete newline-terminated records from the durable Hermes signal log and idempotently reconstructs/promotes the one canonical `signal.created` row per `signal_id`. An incomplete final crash tail is ignored. A malformed committed interior record fails closed and requires operator repair.

`retry-dead-letter` performs one explicit audited delivery attempt using the exact retained bytes. A non-acknowledged one-shot attempt returns to retained dead-letter state. `dead-letter` is an explicit audited operator transition for pending/retryable work.

## Disable behavior

When `HERMES_PIP_EXPORT_ENABLED=false` or unset, Hermes does not require exporter dependencies, signing keys, outbox storage, or PIP network access. The public producer continues to generate the same `HermesSignal` decisions.

## Conformance authority

`tests/pip/pip-contract.lock.json` pins the exact private PIP repository commit used for this implementation. The minimal authority files needed by Hermes CI are vendored under `tests/pip/authority/` and individually bound to their upstream Git blob SHA, so a snapshot cannot drift silently.

CI verifies:

- authority blob integrity and active SignalEnvelope v1 support;
- RFC 8785 golden canonicalization vectors;
- frozen producer-delivery retry/lease/body-size policy;
- a real Hermes-generated `signal.created` envelope against the pinned schema and normative checks;
- SHA-256 event binding and Ed25519 detached signature verification.

This is intentionally scoped evidence for Issue #54's `signal.created` producer surface. Hermes does not manufacture lifecycle evidence for `signal.closed`, `signal.superseded`, `signal.resolved`, or corrections that it does not implement in this issue, and producer runtime code does not import PIP application code.
