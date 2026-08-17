"""Static public producer catalog for supported temperature markets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProducerLocation:
    slug: str
    name: str
    latitude: float
    longitude: float
    station_id: str
    market_timezone: str
    climate_region: str


LOCATIONS: dict[str, ProducerLocation] = {
    "nyc": ProducerLocation(
        slug="nyc",
        name="New York City",
        latitude=40.7772,
        longitude=-73.8726,
        station_id="KLGA",
        market_timezone="America/New_York",
        climate_region="northeast",
    ),
    "chicago": ProducerLocation(
        slug="chicago",
        name="Chicago",
        latitude=41.9742,
        longitude=-87.9073,
        station_id="KORD",
        market_timezone="America/Chicago",
        climate_region="ohio_valley",
    ),
    "miami": ProducerLocation(
        slug="miami",
        name="Miami",
        latitude=25.7959,
        longitude=-80.2870,
        station_id="KMIA",
        market_timezone="America/New_York",
        climate_region="southeast",
    ),
    "dallas": ProducerLocation(
        slug="dallas",
        name="Dallas",
        latitude=32.8471,
        longitude=-96.8518,
        station_id="KDAL",
        market_timezone="America/Chicago",
        climate_region="south",
    ),
    "seattle": ProducerLocation(
        slug="seattle",
        name="Seattle",
        latitude=47.4502,
        longitude=-122.3088,
        station_id="KSEA",
        market_timezone="America/Los_Angeles",
        climate_region="northwest",
    ),
    "atlanta": ProducerLocation(
        slug="atlanta",
        name="Atlanta",
        latitude=33.6407,
        longitude=-84.4277,
        station_id="KATL",
        market_timezone="America/New_York",
        climate_region="southeast",
    ),
}

MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
