"""Runs the Streamlit page with sample data and a fake agent (no API calls) and checks that it works."""

from collections import Counter

import pytest
from streamlit.testing.v1 import AppTest

from searchbrain import agent, db, storage
from searchbrain.models import Startup


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    conn = db.connect()
    db.upsert_startup(conn, Startup(name="Linkerbot", description="Dexterous robotic hands", city="Beijing", country="China",
                                    latitude=39.9, longitude=116.4, location_precision="city", founding_year=2023, industry="robotics"))
    db.upsert_startup(conn, Startup(name="NEURA Robotics", description="Cognitive robots", country="Germany",
                                    latitude=51.1, longitude=10.4, location_precision="country", industry="robotics"))
    db.upsert_startup(conn, Startup(name="Fauna Robotics", description="Home humanoids", location_precision="none"))

    def fake_ask(question, history=None, conn=None, **kwargs):
        return agent.Answer(text=f"Answer to: {question}", startups=[Startup(name="Linkerbot")])

    monkeypatch.setattr(agent, "ask", fake_ask)
    return AppTest.from_file("../app.py", default_timeout=60)


def page_text(app) -> str:
    return " ".join(m.value for m in app.markdown) + " " + " ".join(c.value for c in app.caption)


def test_home_shows_greeting_and_map_stats(app):
    app.run()
    assert not app.exception
    text = page_text(app)
    assert "What are we looking for today?" in text
    assert "3 startups · 2 countries · 2 on the map" in text


def test_question_on_home_starts_a_saved_chat(app):
    app.run()
    app.chat_input(key="home_question").set_value("Which startups build robot hands in China?").run()
    assert not app.exception
    assert app.session_state.view == "chat"
    assert "Answer to: Which startups build robot hands in China?" in page_text(app)

    chats = storage.list_chats(db.connect())
    assert [c["title"] for c in chats] == ["Which startups build robot hands in China?"]
    assert storage.get_chat(db.connect(), chats[0]["id"])["messages"][1]["found"] == ["linkerbot"]


def test_browse_filter_and_save_scout(app):
    app.run()
    app.button(key="nav_browse").click().run()
    app.text_input(key="f_keyword").input("hands").run()
    assert "1 of 3 startups" in page_text(app)

    app.text_input(key="scout_name").input("Hands").run()
    app.button(key="save_scout").click().run()
    assert not app.exception
    assert app.session_state.view == "scout"
    assert "1 startups · 0 new since your last visit" in page_text(app)


COLLECT_CALLS = []


def fake_collect(topic, subtopics=None, on_progress=None, sources=("web",)):
    """Stands in for the real web search: stores one lidar startup."""
    COLLECT_CALLS.append(sources)
    conn = db.connect()
    status = db.upsert_startup(conn, Startup(name="Ouster", description="Digital lidar sensors", country="United States",
                                             topic=topic, yc_batch="W15" if "yc" in sources else None))
    if on_progress:
        on_progress(1, 1, "done")
    return {"yc": int("yc" in sources), "extracted": 1, "new": int(status == "new"), "updated": int(status == "updated"),
            "batches": Counter({"done": 1})}


def test_chat_proposes_web_search_and_runs_it_after_confirm(app, monkeypatch):
    from searchbrain import collect as collect_module

    def ask_with_proposal(question, history=None, conn=None, **kwargs):
        lidar = [s for s in db.search_startups(conn or db.connect(), keyword="lidar")]
        if lidar:
            return agent.Answer(text="Found Ouster.", startups=lidar)
        return agent.Answer(text="No lidar startups yet.", web_search={"topic": "lidar sensors", "subtopics": ["automotive lidar"]})

    monkeypatch.setattr(agent, "ask", ask_with_proposal)
    monkeypatch.setattr(collect_module, "collect", fake_collect)

    app.run()
    app.chat_input(key="home_question").set_value("Which lidar companies exist?").run()
    assert "No lidar startups yet." in page_text(app)
    assert "Search the web for a new area?" in page_text(app)

    chat_id = app.session_state.chat_id
    app.button(key=f"ws_start_{chat_id}_1").click().run()
    assert not app.exception
    assert "Found Ouster." in page_text(app)  # the original question was asked again
    assert "lidar sensors" in [s["name"] for s in storage.saved_searches(db.connect())]  # Scout created
    messages = storage.get_chat(db.connect(), chat_id)["messages"]
    assert messages[1]["web_search"]["status"] == "done"
    assert COLLECT_CALLS[-1] == ("yc", "web")  # YC checkbox is on by default


def test_yc_only_button_skips_the_web(app, monkeypatch):
    from searchbrain import collect as collect_module

    def ask_with_proposal(question, history=None, conn=None, **kwargs):
        lidar = db.search_startups(conn or db.connect(), keyword="lidar")
        if lidar:
            return agent.Answer(text="Found Ouster (YC W15).", startups=lidar)
        return agent.Answer(text="No lidar startups yet.", web_search={"topic": "lidar sensors", "subtopics": []})

    monkeypatch.setattr(agent, "ask", ask_with_proposal)
    monkeypatch.setattr(collect_module, "collect", fake_collect)

    app.run()
    app.chat_input(key="home_question").set_value("Lidar companies?").run()
    app.button(key=f"ws_yc_only_{app.session_state.chat_id}_1").click().run()
    assert not app.exception
    assert COLLECT_CALLS[-1] == ("yc",)
    assert "Found Ouster (YC W15)." in page_text(app)
    assert "1 of them from Y Combinator" in page_text(app)


def test_home_button_returns_to_start_page(app):
    app.run()
    app.button(key="nav_browse").click().run()
    app.button(key="nav_home").click().run()
    assert app.session_state.view == "home"
    assert "What are we looking for today?" in page_text(app)


def test_scout_counts_new_startups(app):
    conn = db.connect()
    storage.save_search(conn, "China", {"countries": ["China"]})
    conn.execute("UPDATE saved_searches SET last_seen_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()

    app.run()
    assert app.button(key="scout_China").label.endswith("1 new")
    app.button(key="scout_China").click().run()
    assert "1 startups · 1 new since your last visit" in page_text(app)


def test_answer_shows_profiles_with_fit_and_details(app, monkeypatch):
    def ask_with_matches(question, history=None, conn=None, **kwargs):
        linkerbot = db.find_startup(conn or db.connect(), Startup(name="Linkerbot"))
        return agent.Answer(
            text="I found 1 potential match. The best fit is Linkerbot.",
            startups=[linkerbot],
            matches=[{"startup": linkerbot, "score": 5, "why": "Builds dexterous robotic hands."}],
        )

    monkeypatch.setattr(agent, "ask", ask_with_matches)
    app.run()
    app.chat_input(key="home_question").set_value("Robot hands?").run()
    assert not app.exception

    text = page_text(app)
    assert "Potential matches (1)" in text
    assert "Fit: 5/5" in text
    assert "Location:** Beijing, China" in text and "Founded:** 2023" in text
    assert "Why it fits:** Builds dexterous robotic hands." in text
    chat = storage.get_chat(db.connect(), app.session_state.chat_id)
    assert chat["messages"][1]["matches"] == [{"key": "linkerbot", "score": 5, "why": "Builds dexterous robotic hands."}]
