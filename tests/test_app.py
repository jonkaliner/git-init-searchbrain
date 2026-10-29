"""Runs the Streamlit page with sample data and a fake agent (no API calls) and checks that it works."""

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

    def fake_ask(question, history=None, conn=None):
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


def test_suggestion_starts_a_saved_chat(app):
    app.run()
    app.button(key="suggest_0").click().run()
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


def test_scout_counts_new_startups(app):
    conn = db.connect()
    storage.save_search(conn, "China", {"countries": ["China"]})
    conn.execute("UPDATE saved_searches SET last_seen_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()

    app.run()
    assert app.button(key="scout_China").label.endswith("1 new")
    app.button(key="scout_China").click().run()
    assert "1 startups · 1 new since your last visit" in page_text(app)
