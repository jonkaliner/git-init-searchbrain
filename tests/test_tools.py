import json

import pytest

from searchbrain import db, tools
from searchbrain.models import Startup
from searchbrain.search import SearchHit


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_FILE", path)
    tools.reset_seen()
    connection = db.connect(path)
    db.upsert_startup(connection, Startup(name="Mimic Robotics", description="AI robotic hand", country="Switzerland", link="https://mimic.com"))
    db.upsert_startup(connection, Startup(name="Linkerbot", summary="Builds a robotic hand and more", country="China"))
    db.upsert_startup(connection, Startup(name="Agility Robotics", description="Humanoid robot Digit", country="United States"))
    return connection


def test_known_startups_best_match_first_with_compact_fields(conn):
    found = json.loads(tools.search_known_startups.invoke({"keywords": "robotic hand"}))
    # Mimic has both words in its one-liner (score 4); Agility matches "robotic" in its name, Linkerbot has both only in
    # the long summary (both score 2, so they are sorted by name)
    assert [row["name"] for row in found] == ["Mimic Robotics", "Agility Robotics", "Linkerbot"]
    assert found[0] =={"name": "Mimic Robotics", "what": "AI robotic hand", "country": "Switzerland", "website": "https://mimic.com"}
    assert "mimic robotics" in tools.seen_text() and "https://mimic.com" in tools.seen_text()


def test_known_startups_do_not_need_every_word(conn):
    # No startup has all three words, but each has two of them. Agility and Mimic have them in the name or one-liner (score 4),
    # Linkerbot only in the long summary (score 2)
    found = json.loads(tools.search_known_startups.invoke({"keywords": "humanoid robotic hand"}))
    assert [row["name"] for row in found] == ["Agility Robotics", "Mimic Robotics", "Linkerbot"]


def test_known_startups_need_at_least_half_of_the_words(conn):
    # only "hand" matches (Mimic and Linkerbot), 1 of 3 words is not enough
    assert "No startup" in tools.search_known_startups.invoke({"keywords": "biotech chip hand"})



def test_known_startups_filter_by_country_and_say_when_empty(conn):
    only_china = json.loads(tools.search_known_startups.invoke({"keywords": "robotic", "countries": "China"}))
    assert [row["name"] for row in only_china] == ["Linkerbot"]
    assert "No startup" in tools.search_known_startups.invoke({"keywords": "biotech"})


def test_program_news_lists_posts_and_remembers_them(conn):
    conn.execute(
        "INSERT INTO feed_entries (id, program, title, link, summary, published, fetched_at) VALUES ('1', 'HAX', 'Robot hand cohort', 'https://hax.co/p', 'Ten startups', '2026-11-03', 'now')"
    )
    conn.commit()
    posts = json.loads(tools.search_program_news.invoke({"keywords": "robot hands"}))
    assert posts == [{"program": "HAX", "title": "Robot hand cohort", "date": "2026-11-03", "url": "https://hax.co/p", "text": "Ten startups"}]
    assert "https://hax.co/p" in tools.seen_text()


def test_web_search_returns_short_results(monkeypatch):
    tools.reset_seen()
    monkeypatch.setattr(tools.search, "search", lambda queries, max_results=10: [SearchHit("Proception", "https://p.com", "x" * 1000, 1.0)])
    result = json.loads(tools.search_web.invoke({"query": "robot hand"}))
    assert result == [{"title": "Proception", "url": "https://p.com", "text": "x" * 300}]
    assert "https://p.com" in tools.seen_text()


def test_web_search_without_results_tells_the_agent_to_go_on(monkeypatch):
    monkeypatch.setattr(tools.search, "search", lambda queries, max_results=10: [])
    assert "Continue" in tools.search_web.invoke({"query": "x"})


def test_read_page_passes_the_text(monkeypatch):
    tools.reset_seen()
    monkeypatch.setattr(tools.web, "page_text", lambda url, limit: f"text of {url}")
    assert tools.read_page.invoke({"url": "https://p.com"}) == "https://p.com\ntext of https://p.com"
    assert "https://p.com" in tools.seen_text()


def test_reset_forgets_everything(conn):
    tools.search_known_startups.invoke({"keywords": "robot"})
    assert "agility" in tools.seen_text()
    tools.reset_seen()
    assert tools.seen_text() == ""


def test_tools_have_descriptions_for_the_model():
    for tool in tools.SCOUT_TOOLS + tools.ANALYST_TOOLS:
        assert len(tool.description) > 40
