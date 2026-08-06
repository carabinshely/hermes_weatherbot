# Market and temperature resolution contracts

This document defines the shared interpretation used by scanning, paper execution,
settlement, learning, and future live adapters. Those layers must not re-parse the
same market into different identifiers, prices, buckets, or dates.

## Market identity

A Polymarket binary market has four distinct concepts:

- **Gamma market ID** — the decimal identifier used by the Gamma market API;
- **condition ID** — the 32-byte hexadecimal condition identifying the market;
- **outcome** — an explicit semantic label, currently `YES` or `NO`;
- **outcome-token ID** — the decimal CLOB asset identifier corresponding to one outcome.

The Gamma `outcomes` and `clobTokenIds` arrays are paired by index, but the code maps
by the normalized outcome label rather than assuming that YES is always first. Missing,
duplicated, unsupported, or length-mismatched mappings fail closed.

A condition ID is never accepted where an outcome-token ID is required.

Official references:

- https://docs.polymarket.com/quickstart/fetching-data
- https://docs.polymarket.com/developers/CLOB/clients/methods-public

## Prices and executable quotes

Gamma `outcomePrices` are descriptive prices for different outcomes. They are not the
bid and ask of one selected outcome and therefore cannot be subtracted to calculate a
spread.

Executable pricing comes only from the CLOB order book for the selected outcome-token
ID. A usable book must:

- identify the expected condition and selected token;
- include at least one bid and one ask;
- have correctly sorted, unique price levels;
- not be crossed or locked;
- be recent enough for the caller's freshness policy;
- contain enough ask depth for the requested share quantity.

The entry price used for expected value and sizing is the size-aware average fill price.
The quote also records the best bid, best ask, worst consumed ask, book timestamp, and
book hash.

Official reference:

- https://docs.polymarket.com/developers/CLOB/prices-books/get-book

## Temperature buckets

Daily temperature markets use finalized whole-degree outcomes. The parser supports:

- `X or below` — a lower tail;
- `between X-Y` — an inclusive bounded whole-degree interval;
- exact `X` — a one-degree interval;
- `X or higher` — an upper tail.

No magic values such as `-999` or `999` represent infinity.

For a normal-distribution forecast model, an inclusive integer outcome `a..b` uses the
continuous interval:

```text
[a - 0.5, b + 0.5)
```

Thus an exact `72°F` outcome uses `[71.5, 72.5)` and has non-zero probability. Lower
and upper tail probabilities are calculated from the same half-degree boundaries.
A complete market partition must have exactly one lower tail and one upper tail, no
overlap, and no gap. Its probabilities must sum to one within numerical tolerance.

Point forecasts are classified with decimal half-up rounding before selecting the
whole-degree outcome. Authoritative settlement input must already be a whole-degree
value; a fractional or unverified result fails closed.

The market question and its linked resolution source remain authoritative for a
specific market. The current weather-market pages describe final settlement from the
specified Weather Underground station's finalized daily high, so future resolver code
must retain the market's source URL and station metadata rather than infer them from a
city name alone.

## Local market dates

Each city has an explicit IANA timezone. `today`, D+0, D+1, forecast joins, observation
joins, and the market event date use that city's local calendar day.

Stored forecast and resolution records contain both:

- the timezone-aware UTC retrieval timestamp; and
- the qualified local market date and IANA timezone.

A bare date string is insufficient for joining data. Naive timestamps, unknown
 timezones, duplicated local dates, and cross-timezone joins fail closed.

## Resolver dependency

The authoritative resolution worker in issue #13 must consume these contracts rather
than implement another parser. It will:

1. load a persisted market identity and temperature partition;
2. retrieve the market's declared authoritative source;
3. validate source time, station, local date, unit, and finalized whole-degree value;
4. map the value through the same partition used at signal time;
5. append one idempotent immutable resolution event to the durable ledger.
