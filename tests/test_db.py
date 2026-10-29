import sqlite3

from searchbrain import db
from searchbrain.models import Startup


def test_insert_and_read_back(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    assert db.upsert_startup(conn, Startup(name="Mimic Robotics", country="Switzerland", link="https://www.mimicrobotics.com")) == "new"
    assert db.upsert_startup(conn, Startup(name="Tangent Robotics", city="New York City")) == "new"

    names = [s.name for s in db.all_startups(conn)]
    assert names == ["Mimic Robotics", "Tangent Robotics"]


def test_same_company_twice_is_updated_not_duplicated(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.upsert_startup(conn, Startup(name="Mimic Robotics", city="Zurich", link="https://mimicrobotics.com"))
    result = db.upsert_startup(conn, Startup(name="Mimic", founding_year=2024, link="http://www.mimicrobotics.com/about"))

    startups = db.all_startups(conn)
    assert result == "updated"
    assert len(startups) == 1
    assert startups[0].city == "Zurich"  # kept, although the second record had no city
    assert startups[0].founding_year == 2024  # filled in from the second record


def test_same_company_with_and_without_link_is_one_row(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.upsert_startup(conn, Startup(name="Agility Robotics", link="https://agilityrobotics.com"))
    result = db.upsert_startup(conn, Startup(name="Agility Robotics", country="United States"))

    startups = db.all_startups(conn)
    assert result == "updated"
    assert len(startups) == 1
    assert startups[0].country == "United States"
    assert db.find_startup(conn, Startup(name="Agility Robotics Inc")).link == "https://agilityrobotics.com"


def test_merge_existing_duplicates_keeps_best_location(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    # Two rows of the same company, as created by the old key logic (one with link, one without)
    for key, startup in [
        ("neura-robotics.com", Startup(name="NEURA Robotics", link="https://neura-robotics.com", location_precision="none")),
        ("neurarobotics", Startup(name="NEURA Robotics", country="Germany", latitude=51.1, longitude=10.4, location_precision="country", founding_year=2019)),
    ]:
        conn.execute(
            "INSERT INTO startups (key, name_key, name, link, country, latitude, longitude, location_precision, founding_year, created_at, updated_at)"
            " VALUES (?, 'neurarobotics', ?, ?, ?, ?, ?, ?, ?, 'now', 'now')",
            (key, startup.name, startup.link, startup.country, startup.latitude, startup.longitude, startup.location_precision, startup.founding_year),
        )

    assert db.merge_duplicates(conn) == ["NEURA Robotics"]

    startups = db.all_startups(conn)
    assert len(startups) == 1
    neura = startups[0]
    assert neura.link == "https://neura-robotics.com"
    assert neura.founding_year == 2019
    assert (neura.country, neura.location_precision, neura.latitude) == ("Germany", "country", 51.1)


def test_old_database_gets_name_key_column(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE startups (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created_at TEXT, updated_at TEXT)")
    old.execute("INSERT INTO startups (key, name, created_at, updated_at) VALUES ('x.com', 'Proception Inc', 'now', 'now')")
    old.commit()
    old.close()

    conn = db.connect(path)
    assert conn.execute("SELECT name_key FROM startups").fetchone()["name_key"] == "proception"


def test_key_without_link_uses_name():
    assert db.startup_key(Startup(name="Stella-Robot")) == "stellarobot"
    assert db.startup_key(Startup(name="X", link="www.Example.com/page")) == "example.com"
