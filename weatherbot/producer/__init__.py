"""Public non-executing Hermes signal producer."""

from weatherbot.producer.config import ProducerPolicy, load_producer_policy
from weatherbot.producer.model import (
    CalibratedMarketCandidate,
    HermesSignal,
    SignalMarketReference,
    make_signal_id,
)
from weatherbot.producer.service import evaluate_candidate

__all__ = [
    "CalibratedMarketCandidate",
    "HermesSignal",
    "ProducerPolicy",
    "SignalMarketReference",
    "evaluate_candidate",
    "load_producer_policy",
    "make_signal_id",
]
