"""The Startup record that all parts of Searchbrain share."""

from dataclasses import dataclass


@dataclass
class Startup:
    name: str
    description: str = ""
    industry: str | None = None
    country: str | None = None
    city: str | None = None
    founding_year: int | None = None
    link: str | None = None
    source_url: str | None = None
    summary: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # "city", "country" or "none" after geocoding; None means not geocoded yet
    location_precision: str | None = None
    topic: str | None = None
    yc_batch: str | None = None  # e.g. "W24" for Y Combinator Winter 2024, None if not a YC company
