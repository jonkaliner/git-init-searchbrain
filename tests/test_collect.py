from searchbrain import collect, db, worldmap
from searchbrain.models import Startup


def test_fill_from_db_keeps_city_from_earlier_run(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.upsert_startup(conn, Startup(name="Linkerbot", city="Beijing", country="China", summary="Old summary"))

    found_again = Startup(name="Linkerbot", country="China")
    collect.fill_from_db(conn, found_again)

    assert found_again.city == "Beijing"
    assert found_again.summary == "Old summary"


def test_summarize_uses_model_answer(monkeypatch):
    monkeypatch.setattr(collect.llm, "chat", lambda system, user: '{"summaries": ["Builds robot hands."]}')
    startups = [Startup(name="Proception", description="desc")]
    collect.summarize(startups)
    assert startups[0].summary == "Builds robot hands."


def test_summarize_falls_back_to_description(monkeypatch):
    monkeypatch.setattr(collect.llm, "chat", lambda system, user: "not json")
    startups = [Startup(name="Proception", description="Makes ProHand 1.0.")]
    collect.summarize(startups)
    assert startups[0].summary == "Makes ProHand 1.0."


def test_topic_queries_add_subtopics():
    queries = collect.topic_queries("humanoid robotics", ["robot hand"])
    assert "robot hand startup humanoid robotics" in queries
    assert len(queries) == 4


def test_map_has_city_markers_and_one_marker_per_country(tmp_path):
    startups = [
        Startup(name="Linkerbot", latitude=39.9, longitude=116.4, location_precision="city"),
        Startup(name="BrainCo", country="China", latitude=34.5, longitude=108.9, location_precision="country"),
        Startup(name="Stella-Robot", country="China", latitude=34.5, longitude=108.9, location_precision="country"),
        Startup(name="Fauna Robotics", location_precision="none"),
    ]
    html = worldmap.save_map(startups, tmp_path / "map.html").read_text(encoding="utf-8")

    assert "Linkerbot" in html
    assert html.count("China: 2 startup(s), city unknown") == 1
    assert "Fauna Robotics" not in html


def test_popup_escapes_html_and_blocks_bad_links():
    popup = worldmap._popup_html(Startup(name="<script>x</script>", link="javascript:alert(1)"))
    assert "<script>" not in popup
    assert "javascript:" not in popup
