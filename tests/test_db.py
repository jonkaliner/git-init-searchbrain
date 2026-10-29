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


def test_key_without_link_uses_name():
    assert db.startup_key(Startup(name="Stella-Robot")) == "stellarobot"
    assert db.startup_key(Startup(name="X", link="www.Example.com/page")) == "example.com"
