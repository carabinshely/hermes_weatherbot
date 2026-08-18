# PIP publication

Hermes publishes only already-made public `HermesSignal v1` decisions to Prediction Intelligence Platform (PIP). PIP delivery is downstream infrastructure and cannot change forecast probability, strategy policy, signal identity, PAPER experiments, sizing, risk, or execution behavior.

## Runtime boundary

```text
weather/market/calibration evidence
        -> evaluate_candidate()
        -> immutable HermesSignal
        -> fsync signal JSONL
        -> build/sign/freeze SignalEnvelope
        -> durable SQLite outbox
        ---------------- async boundary ----------------
        -> python -m weatherbot.pip run
        -> POST /v1/events
        -> accepted / already_accepted / retry / rejected
```

The producer process never waits for PIP HTTP delivery. Exporter configuration is not part of `ProducerPolicy` and therefore cannot change `policy_fingerprint` or `signal_id`.

## Contract

Issue #54 exports one PIP lifecycle event type: `signal.created`.

- `HermesSignal.condition_id` becomes PIP `market.market_id`.
- `HermesSignal.token_id` becomes PIP `market.outcome_id`.
- `model_probability` becomes `forecast.probability`.
- `market_reference.all_in_reference_price` becomes the executable market reference value.
- `market_reference.probability_edge` becomes decision edge.
- `evidence.mode` is `live`, meaning contemporaneous real producer evidence; it does **not** mean live trading or proof of execution.
- weather/calibration/source details stay under `hermes_weatherbot:weather:v1`.
- producer public/paid eligibility is explicitly `not_asserted` in the extension. PIP owns downstream eligibility decisions.

Only the top-level `event` object is canonicalized with RFC 8785 JCS (JSON Canonicalization Scheme) and signed. The detached signature is not part of `event_sha256`.

## Signing key

The required key is a dedicated Ed25519 application-identity key. It must have no wallet, exchange-write, transaction-signing, or funds-control authority.

Install the exporter dependencies separately from the minimal and historical live profiles:

```bash
uv pip install -r requirements-pip-export.txt
```

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

`config/producer-releases/<strategy_version>.json` binds each immutable strategy version to the exact repository revision and decision-code identity. Its raw bytes are SHA-256 hashed and carried as the code artifact digest.

Recovery of an old signal therefore never substitutes the current checkout revision. Missing or mismatched release provenance fails PIP publication closed without altering the Hermes signal.

## Outbox semantics

The PIP outbox is separate from the historical financial/event ledger.

SQLite uses WAL (Write-Ahead Logging), `synchronous=FULL`, and atomic `BEGIN IMMEDIATE` claims. Frozen identity columns are protected by a database trigger.

States:

```text
pending -> in_flight -> acknowledged
                    \-> retry_wait -> in_flight
                    \-> dead_letter
```

Automatic claims use a unique fencing token and a maximum 60-second lease. An expired automatic lease is recovered as an ambiguous retry while still inside the seven-day delivery horizon. At horizon expiry, undelivered items remain retained as `dead_letter`; they are never silently deleted or acknowledged.

Local enqueue is idempotent:

```text
same (producer_id,event_id) + same digest/bytes -> existing item
same (producer_id,event_id) + different bytes   -> fail closed
```

Retries always resend the exact persisted `envelope_bytes`; they never rebuild or re-sign the event. This is required for safe recovery when PIP accepted a request but Hermes crashed before persisting the acknowledgement.

## HTTP result rules

Production endpoints must use HTTPS. Redirects are never followed.

A response acknowledges an item only when the body is a valid bound `pip.event-delivery-result` v1 with matching `producer_id`, `event_id`, and `event_sha256`, and disposition `accepted` or `already_accepted` with a receipt ID.

A bare `2xx`, `202`, malformed body, mismatched binding, unknown result, timeout, redirect, `429`, or `5xx` is not acknowledgement. Ambiguous outcomes remain retryable. Only a valid bound `rejected` result becomes a permanent dead letter.

Decoded response bodies are bounded to 65,536 bytes before JSON interpretation.

## Operations

```bash
python -m weatherbot.pip status
python -m weatherbot.pip reconcile
python -m weatherbot.pip deliver-once
python -m weatherbot.pip run
```

`reconcile` scans complete newline-terminated records from the durable Hermes signal log and idempotently reconstructs missing outbox rows. An incomplete final crash tail is ignored; a malformed committed interior record fails closed and requires operator repair.

## Disable behavior

When `HERMES_PIP_EXPORT_ENABLED=false` or unset, Hermes does not require exporter dependencies, signing keys, outbox storage, or PIP network access. The public producer continues to generate the same `HermesSignal` decisions.

## Conformance authority

`tests/pip/pip-contract.lock.json` pins the exact PIP repository commit used for contract/conformance work. Producer runtime code does not import PIP application code.
