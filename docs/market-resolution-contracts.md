# Market and temperature interpretation contracts

This document defines the shared read-only market interpretation used by public signal
production, calibration/observation tooling, internal PAPER experiments, and retained
internal resolution primitives. It is not an execution roadmap.

## Market identity

A Polymarket binary market keeps four distinct concepts:

- Gamma market ID;
- condition ID;
- semantic outcome (`YES` / `NO`);
- outcome-token ID used by the CLOB order book.

Outcome labels and token IDs are paired explicitly. Missing, duplicated, unsupported, or
length-mismatched mappings fail closed. A condition ID is never accepted where an outcome-
token ID is required.

## Prices and read-only executable reference

Gamma outcome prices are descriptive values, not the bid and ask of one selected outcome.
Signal edge uses selected-token CLOB order-book evidence. A usable book must identify the
expected condition/token, contain valid uncrossed levels, satisfy freshness policy, and
provide the configured public reference depth.

The public producer's reference notional is a read-only liquidity/economics probe, not a
bet size or order instruction. Internal PAPER may freeze order-book evidence and reuse the
same depth/fee/slippage primitives for hypothetical fills.

## Temperature buckets

Daily temperature markets use finalized whole-degree outcomes. Supported shapes include
lower tail, bounded inclusive interval, exact degree, and upper tail. Inclusive integer
outcomes are represented with half-degree continuous boundaries; exact `72°F`, for
example, uses `[71.5, 72.5)`.

A complete partition must have exactly one lower tail and one upper tail with no overlap or
gap. Model probabilities across a complete partition must sum to one within numerical
tolerance.

## Local market dates

Each city uses an explicit IANA timezone. Forecast joins, target dates, observation joins,
and market event dates are qualified by both local date and timezone. Naive timestamps,
unknown timezones, duplicated local dates, and cross-timezone joins fail closed.

## Resolution responsibility

Hermes may reuse these contracts for calibration observations and retained internal PAPER
resolution primitives. PIP independently resolves and scores the real emitted-signal
history after receipt; Hermes internal financial-ledger settlement is not that public track
record.
