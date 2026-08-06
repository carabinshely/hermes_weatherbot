from __future__ import annotations

from decimal import Decimal

import pytest

from tests.persistence.helpers import (
    acknowledged,
    cancelled,
    fill,
    intent_created,
    market_resolved,
    position_settled,
    rejected,
    submitted,
    unknown,
)
from weatherbot.domain import AccountOpened, Money
from weatherbot.persistence.codec import (
    decode_event,
    decode_metadata,
    encode_event,
    encode_metadata,
)
from weatherbot.persistence.errors import CorruptLedgerError, SchemaVersionError


def all_event_types() -> tuple[object, ...]:
    intent = intent_created()
    return (
        AccountOpened(
            event_id=intent.event_id.__class__("account-opened-codec"),
            occurred_at=intent.occurred_at,
            initial_cash=Money.of("100"),
        ),
        intent,
        submitted(intent),
        acknowledged(intent),
        fill(intent),
        rejected(intent),
        cancelled(intent),
        unknown(intent),
        market_resolved(),
        position_settled(),
    )


@pytest.mark.parametrize("event", all_event_types())
def test_every_ledger_event_round_trips_deterministically(event: object) -> None:
    encoded = encode_event(event)  # type: ignore[arg-type]
    decoded = decode_event(encoded.payload_json)
    reencoded = encode_event(decoded)

    assert decoded == event
    assert reencoded.payload_json == encoded.payload_json
    assert reencoded.payload_hash == encoded.payload_hash
    assert reencoded.event_type == encoded.event_type


def test_metadata_is_canonical_and_preserves_decimal_as_text() -> None:
    payload, payload_hash = encode_metadata(
        {
            "z": [3, Decimal("0.123456789"), True],
            "a": {"backend_order": "paper-1"},
        }
    )

    assert payload == ('{"a":{"backend_order":"paper-1"},"z":[3,"0.123456789",true]}')
    assert decode_metadata(payload, payload_hash) == {
        "a": {"backend_order": "paper-1"},
        "z": [3, "0.123456789", True],
    }


def test_metadata_rejects_floats_and_sdk_objects() -> None:
    with pytest.raises(TypeError, match="floating-point"):
        encode_metadata({"price": 0.5})

    with pytest.raises(TypeError, match="unsupported value type"):
        encode_metadata({"client": object()})


def test_metadata_hash_mismatch_fails_closed() -> None:
    payload, _ = encode_metadata({"backend": "paper"})

    with pytest.raises(CorruptLedgerError, match="metadata hash mismatch"):
        decode_metadata(payload, "0" * 64)


def test_duplicate_json_keys_fail_closed() -> None:
    payload = (
        '{"event_schema_version":1,"event_schema_version":1,'
        '"event_type":"account_opened","data":{}}'
    )

    with pytest.raises(CorruptLedgerError, match="duplicate key"):
        decode_event(payload)


def test_unknown_event_schema_fails_closed() -> None:
    intent = intent_created()
    encoded = encode_event(intent)
    payload = encoded.payload_json.replace(
        '"event_schema_version":1',
        '"event_schema_version":999',
    )

    with pytest.raises(SchemaVersionError, match="unsupported"):
        decode_event(payload)
