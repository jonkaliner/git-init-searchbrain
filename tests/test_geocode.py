from types import SimpleNamespace

from geopy.exc import GeocoderTimedOut

from searchbrain import db
from searchbrain.geocode import Geocoder, cache_key
from searchbrain.models import Startup

KNOWN = {
    ("Zurich", "Switzerland"): (47.37, 8.54),
    (None, "Switzerland"): (46.80, 8.23),
    (None, "China"): (35.00, 105.00),
}


class FakeNominatim:
    """Answers from the KNOWN table and counts how often it was asked."""

    def __init__(self):
        self.calls = []

    def __call__(self, query: dict):
        self.calls.append(query)
        coords = KNOWN.get((query.get("city"), query.get("country")))
        return SimpleNamespace(latitude=coords[0], longitude=coords[1]) if coords else None


def make(tmp_path):
    fake = FakeNominatim()
    return Geocoder(db.connect(tmp_path / "test.db"), lookup=fake), fake


def test_cache_key():
    assert cache_key("Berlin", "Germany") == "berlin|germany"
    assert cache_key(None, " Germany ") == "|germany"


def test_city_known(tmp_path):
    geocoder, _ = make(tmp_path)
    startup = Startup(name="Mimic Robotics", city="Zurich", country="Switzerland")
    geocoder.geocode(startup)
    assert (startup.latitude, startup.longitude, startup.location_precision) == (47.37, 8.54, "city")


def test_only_country_known(tmp_path):
    geocoder, _ = make(tmp_path)
    startup = Startup(name="BrainCo", country="China")
    geocoder.geocode(startup)
    assert startup.location_precision == "country"
    assert startup.latitude == 35.00


def test_unknown_city_falls_back_to_country(tmp_path):
    geocoder, _ = make(tmp_path)
    startup = Startup(name="X", city="Nowhere Town", country="Switzerland")
    geocoder.geocode(startup)
    assert startup.location_precision == "country"


def test_nothing_known(tmp_path):
    geocoder, fake = make(tmp_path)
    startup = Startup(name="Fauna Robotics")
    geocoder.geocode(startup)
    assert startup.location_precision == "none"
    assert startup.latitude is None
    assert fake.calls == []


def test_second_lookup_uses_cache_also_for_not_found(tmp_path):
    geocoder, fake = make(tmp_path)
    for _ in range(2):
        geocoder.geocode(Startup(name="A", city="Zurich", country="Switzerland"))
        geocoder.geocode(Startup(name="B", city="Nowhere Town"))
    assert len(fake.calls) == 2  # Zurich once, Nowhere Town once


def test_network_error_leaves_startup_for_retry(tmp_path):
    def broken(query):
        raise GeocoderTimedOut("timeout")

    geocoder = Geocoder(db.connect(tmp_path / "test.db"), lookup=broken)
    startup = Startup(name="A", city="Zurich", country="Switzerland")
    geocoder.geocode(startup)
    assert startup.location_precision is None  # not "none": will be tried again later
