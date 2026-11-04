from searchbrain import collect, db, yc

COMPANIES = [
    {"name": "Ouster", "status": "Active", "one_liner": "Digital lidar sensors", "long_description": "Ouster builds lidar. It is based in SF.",
     "tags": ["Hardware"], "subindustry": "Industrials -> Manufacturing", "batch": "Winter 2015",
     "all_locations": "San Francisco, CA, USA", "website": "https://ouster.com", "url": "https://www.ycombinator.com/companies/ouster"},
    {"name": "OldLidar", "status": "Inactive", "one_liner": "Lidar sensor", "batch": "Summer 2012"},
    {"name": "Robo Hands", "status": "Active", "one_liner": "Robot hands", "long_description": "We also use a lidar sensor.",
     "tags": ["Robotics"], "batch": "Spring 2025", "all_locations": "London, England, United Kingdom"},
    {"name": "Payments Co", "status": "Active", "one_liner": "Payments for shops", "batch": "Fall 2024", "all_locations": "Remote"},
]


def test_short_batch_and_location():
    assert yc.short_batch("Winter 2024") == "W24"
    assert yc.short_batch("Spring 2025") == "X25"
    assert yc.short_batch("other") is None
    assert yc.parse_location("San Francisco, CA, USA; Burlingame, CA, USA") == ("San Francisco", "United States")
    assert yc.parse_location("Remote") == (None, None)
    assert yc.parse_location("Germany") == (None, "Germany")


def test_find_startups_only_active_and_best_matches_first():
    found = yc.find_startups("lidar sensors", companies=COMPANIES)
    assert [s.name for s in found] == ["Ouster", "Robo Hands"]  # OldLidar is inactive, one-liner matches come first
    ouster = found[0]
    assert (ouster.city, ouster.country, ouster.yc_batch, ouster.industry) == ("San Francisco", "United States", "W15", "manufacturing")
    assert ouster.source_url == "https://www.ycombinator.com/companies/ouster"
    assert ouster.summary == "Ouster builds lidar. It is based in SF."


def test_subtopics_widen_the_search():
    assert [s.name for s in yc.find_startups("battery recycling", ["payments"], companies=COMPANIES)] == ["Payments Co"]


class FakeGeocoder:
    """Gives every startup with a city a position, like a successful Nominatim lookup."""

    def __init__(self):
        self.requests = 0
        self.geocoded = []

    def geocode(self, startup):
        self.geocoded.append(startup.name)
        self.requests += 1
        startup.latitude, startup.longitude = 1.0, 2.0
        startup.location_precision = "city" if startup.city else "none"


def test_import_all_stores_every_active_company_and_geocodes_only_new_ones(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    geocoder = FakeGeocoder()

    first = yc.import_all(conn, COMPANIES, geocoder)
    assert (first["new"], first["updated"]) == (3, 0)  # OldLidar is inactive
    stored = {s.name: s for s in db.all_startups(conn)}
    assert set(stored) == {"Ouster", "Robo Hands", "Payments Co"}
    assert stored["Ouster"].location_precision == "city" and stored["Payments Co"].location_precision == "none"

    second = yc.import_all(conn, COMPANIES, geocoder)
    assert (second["new"], second["updated"]) == (0, 3)
    assert len(geocoder.geocoded) == 3  # the second import did not geocode anything again


def test_import_all_keeps_a_known_topic_and_location(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    first_run = yc.find_startups("lidar sensors", companies=COMPANIES)[0]  # Ouster, topic "lidar sensors"
    first_run.latitude, first_run.longitude, first_run.location_precision = 37.7, -122.4, "city"
    db.upsert_startup(conn, first_run)

    geocoder = FakeGeocoder()
    yc.import_all(conn, COMPANIES, geocoder)

    ouster = next(s for s in db.all_startups(conn) if s.name == "Ouster")
    assert ouster.topic == "lidar sensors" and ouster.latitude == 37.7
    assert "Ouster" not in geocoder.geocoded


def test_collect_from_yc_only_needs_no_web_search(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(yc, "load_companies", lambda: COMPANIES)
    monkeypatch.setattr(collect, "geocode_all", lambda conn, startups: 0)
    monkeypatch.setattr(collect, "save_map", lambda startups: tmp_path / "map.html")

    def no_web(*args, **kwargs):
        raise AssertionError("the web must not be searched")

    monkeypatch.setattr(collect, "search", no_web)
    report = collect.collect("lidar sensors", sources=("yc",))

    assert report["yc"] == 2 and report["new"] == 2
    stored = {s.name: s for s in db.all_startups(db.connect())}
    assert stored["Ouster"].yc_batch == "W15"
    assert stored["Ouster"].topic == "lidar sensors"
