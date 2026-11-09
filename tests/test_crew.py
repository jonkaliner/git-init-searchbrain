import json

import pytest

from searchbrain import crew, db, scouts, tools


class FakeGeocoder:
    """Gives every startup with a city a position, like a successful Nominatim lookup."""

    def __init__(self):
        self.geocoded = []

    def geocode(self, startup):
        self.geocoded.append(startup.name)
        startup.latitude, startup.longitude = 47.4, 8.5
        startup.location_precision = "city" if startup.city else "country"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    return db.connect(tmp_path / "test.db")


def tools_showed(monkeypatch, text):
    monkeypatch.setattr(tools, "_seen", [text])


SCOUT_ANSWER = json.dumps(
    {
        "candidates": [
            {"name": "Mimic Robotics", "website": "https://mimic.ai", "description": "AI robotic hand", "country": "Switzerland", "city": "Zurich",
             "founding_year": "2024", "source_url": "https://hax.co/post", "evidence": "seed round"},
            {"name": "Ghost Corp", "website": "https://ghost.com", "description": "made up", "country": "France"},
            {"name": "Tangent", "website": "https://tangent-fake.com", "description": "robot hands", "country": "United States", "source_url": "https://invented.com/x"},
        ]
    }
)
SEEN = "mimic robotics https://mimic.ai ai robotic hand zurich https://hax.co/post tangent robot hands at hax"


def test_parse_list_reads_json_inside_text_and_explains_errors():
    assert crew.parse_list('Here you go: {"matches": [{"name": "A"}, "junk"]} Done.', "matches") == [{"name": "A"}]
    for bad in ["no json at all", '{"matches": "not a list"}', '{"matches": [1,}']:
        with pytest.raises(ValueError):
            crew.parse_list(bad, "matches")


def test_candidate_must_have_been_shown_by_a_tool(monkeypatch):
    seen = SEEN.lower()
    assert crew.check_candidate({"name": "Ghost Corp"}, seen, "t") is None
    assert crew.check_candidate({"name": ""}, seen, "t") is None

    mimic = crew.check_candidate({"name": "Mimic Robotics", "website": "https://mimic.ai", "founding_year": "2024", "source_url": "https://hax.co/post"}, seen, "t")
    assert (mimic.link, mimic.founding_year, mimic.source_url, mimic.topic) == ("https://mimic.ai", 2024, "https://hax.co/post", "t")

    tangent = crew.check_candidate({"name": "Tangent", "website": "https://tangent-fake.com", "source_url": "https://invented.com/x"}, seen, "t")
    assert tangent.name == "Tangent" and tangent.link is None and tangent.source_url is None  # name seen, website and source made up


def test_deal_flow_saves_checked_startups_and_matches(conn, monkeypatch):
    tools_showed(monkeypatch, SEEN)
    scout_id = scouts.add_scout(conn, "Build a deal flow for robot hands")
    ratings = json.dumps(
        {
            "matches": [
                {"name": "Mimic Robotics", "score": 5, "why": "Seed-stage hand company.", "concerns": ""},
                {"name": "Tangent", "score": "3", "why": "Related.", "concerns": "Stage unknown."},
                {"name": "Ghost Corp", "score": 4, "why": "Was thrown away by the checks."},
                {"name": "Invented By Analyst", "score": 5, "why": "Not in the Scout's list."},
            ]
        }
    )
    geocoder = FakeGeocoder()

    result = crew.build_deal_flow(conn, scout_id, "Build a deal flow for robot hands", SCOUT_ANSWER, ratings, geocoder)

    assert result.candidates == 3 and result.rejected == ["Ghost Corp"]
    assert result.new_startups == 2 and geocoder.geocoded == ["Mimic Robotics", "Tangent"]  # Tangent has only a country: still geocoded
    assert [(m["name"], m["score"]) for m in result.matches] == [("Mimic Robotics", 5), ("Tangent", 3)]
    stored = {s.name: s for s in db.all_startups(conn)}
    assert set(stored) == {"Mimic Robotics", "Tangent"} and stored["Mimic Robotics"].city == "Zurich"
    saved = scouts.matches_of(conn, scout_id)
    assert [(m["score"], m["concerns"]) for m in saved] == [(5, None), (3, "Stage unknown.")]
    assert scouts.all_scouts(conn)[0]["last_run_at"]


def test_scores_written_in_other_ways_are_understood(conn, monkeypatch):
    tools_showed(monkeypatch, SEEN)
    scout_id = scouts.add_scout(conn, "robot hands")
    ratings = json.dumps({"matches": [{"name": "Mimic Robotics", "score": "4/5", "why": "x"}, {"name": "Tangent", "score": 3.0, "why": "y"}]})

    result = crew.build_deal_flow(conn, scout_id, "robot hands", SCOUT_ANSWER, ratings, FakeGeocoder())

    assert [(m["name"], m["score"]) for m in result.matches] == [("Mimic Robotics", 4), ("Tangent", 3)]


def test_low_scores_are_not_matches_and_scores_are_capped(conn, monkeypatch):
    tools_showed(monkeypatch, SEEN)
    scout_id = scouts.add_scout(conn, "robot hands")
    ratings = json.dumps({"matches": [{"name": "Mimic Robotics", "score": 9, "why": "x"}, {"name": "Tangent", "score": 1, "why": "not a fit"}]})

    result = crew.build_deal_flow(conn, scout_id, "robot hands", SCOUT_ANSWER, ratings, FakeGeocoder())

    assert [(m["name"], m["score"]) for m in result.matches] == [("Mimic Robotics", 5)]


def test_broken_answers_are_reported_not_raised(conn, monkeypatch):
    tools_showed(monkeypatch, SEEN)
    scout_id = scouts.add_scout(conn, "robot hands")

    result = crew.build_deal_flow(conn, scout_id, "robot hands", "I could not find anything", "also no json", FakeGeocoder())

    assert result.candidates == 0 and result.matches == []
    assert len(result.problems) == 2 and result.problems[0].startswith("Scout:") and result.problems[1].startswith("Analyst:")


def test_known_startups_keep_their_database_details(conn, monkeypatch):
    from searchbrain.models import Startup

    db.upsert_startup(conn, Startup(name="Mimic Robotics", city="Zurich", country="Switzerland", latitude=1.0, longitude=2.0, location_precision="city", yc_batch="W24", link="https://mimic.ai"))
    tools_showed(monkeypatch, SEEN)
    scout_id = scouts.add_scout(conn, "robot hands")
    geocoder = FakeGeocoder()

    result = crew.build_deal_flow(
        conn, scout_id, "robot hands", json.dumps({"candidates": [{"name": "Mimic Robotics"}]}), json.dumps({"matches": [{"name": "Mimic Robotics", "score": 4, "why": "ok"}]}), geocoder
    )

    assert result.new_startups == 0 and geocoder.geocoded == []  # already stored with a position
    mimic = db.all_startups(conn)[0]
    assert (mimic.yc_batch, mimic.latitude) == ("W24", 1.0)
    assert result.matches[0]["startup_key"] == "mimic.ai"


def test_the_crew_has_two_agents_with_the_right_tools(monkeypatch):
    monkeypatch.setattr(crew.config, "get", lambda name: "test-key")  # no real key in tests, and nothing is sent
    team = crew.build_crew("Build a deal flow for robot hands")
    scout, analyst = team.agents
    assert [a.role for a in team.agents] == ["Startup Scout", "Startup Analyst"]
    assert {t.name for t in scout.tools} == {"search_known_startups", "search_program_news", "search_portfolios", "search_web", "read_page"}
    assert {t.name for t in analyst.tools} == {"search_web", "read_page"}
    assert "robot hands" in team.tasks[0].description and team.tasks[1].context == [team.tasks[0]]
