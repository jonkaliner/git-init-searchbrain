"""Step 6: turn city and country into coordinates with Nominatim (OpenStreetMap).

Usage policy: max 1 request per second, identify the app in the user agent,
cache results. Every location is looked up only once, also when nothing was found.
"""

import sqlite3
from datetime import datetime, timezone

from geopy.exc import GeopyError

from searchbrain import config
from searchbrain.log import get_logger
from searchbrain.models import Startup

logger = get_logger(__name__)


def make_geocoder():
    """Nominatim behind a rate limiter: at least 1 second between requests."""
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    nominatim = Nominatim(user_agent=config.get("NOMINATIM_USER_AGENT"), timeout=10)
    return RateLimiter(nominatim.geocode, min_delay_seconds=1, max_retries=1, swallow_exceptions=False)


def cache_key(city: str | None, country: str | None) -> str:
    """e.g. "berlin|germany", or "|germany" when only the country is known."""
    return f"{(city or '').strip().lower()}|{(country or '').strip().lower()}"


class Geocoder:
    """Looks up locations, using the SQLite cache first."""

    def __init__(self, conn: sqlite3.Connection, lookup=None):
        self.conn = conn
        self._lookup = lookup  # created on first network request; tests pass a fake
        self.requests = 0

    def locate(self, city: str | None, country: str | None) -> tuple[float, float] | None:
        """Return (latitude, longitude) or None. Raises GeopyError on network problems."""
        key = cache_key(city, country)
        row = self.conn.execute("SELECT latitude, longitude, found FROM geocode_cache WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return (row["latitude"], row["longitude"]) if row["found"] else None

        if self._lookup is None:
            self._lookup = make_geocoder()
        query = {"country": country} if not city else {"city": city, **({"country": country} if country else {})}
        self.requests += 1
        location = self._lookup(query)

        coords = (location.latitude, location.longitude) if location else None
        self.conn.execute(
            "INSERT OR REPLACE INTO geocode_cache (key, latitude, longitude, found, looked_up_at) VALUES (?, ?, ?, ?, ?)",
            (
                key,
                coords[0] if coords else None,
                coords[1] if coords else None,
                int(coords is not None),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()
        return coords

    def geocode(self, startup: Startup) -> None:
        """Fill latitude, longitude and location_precision of one startup.

        City known -> precision "city"; if the city is not found, fall back to the country.
        Only country known -> precision "country". Nothing known -> "none".
        On network errors the startup stays un-geocoded (None) so a later run can retry.
        """
        try:
            if startup.city and (coords := self.locate(startup.city, startup.country)):
                precision = "city"
            elif startup.country and (coords := self.locate(None, startup.country)):
                precision = "country"
            else:
                coords, precision = None, "none"
        except GeopyError as error:
            logger.error("Geocoding failed for %s (%s, %s): %s", startup.name, startup.city, startup.country, error)
            return

        startup.latitude, startup.longitude = coords if coords else (None, None)
        startup.location_precision = precision


def geocode_all(conn: sqlite3.Connection, startups: list[Startup]) -> int:
    """Geocode a list of startups in place. Returns the number of network requests made."""
    geocoder = Geocoder(conn)
    for startup in startups:
        geocoder.geocode(startup)
    return geocoder.requests
