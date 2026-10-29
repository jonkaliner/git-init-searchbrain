import json
from types import SimpleNamespace

from searchbrain import agent, db
from searchbrain.models import Startup


def make_db(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.upsert_startup(conn, Startup(name="Linkerbot", description="Dexterous robotic hands", city="Beijing", country="China", source_url="https://a.com"))
    db.upsert_startup(conn, Startup(name="Mimic Robotics", description="AI robotic hand", country="Switzerland", founding_year=2024))
    db.upsert_startup(conn, Startup(name="Agility Robotics", description="Humanoid robot Digit", country="United States"))
    return conn


def test_search_keyword_words_match_separately(tmp_path):
    conn = make_db(tmp_path)
    names = [s.name for s in db.search_startups(conn, keyword="robot hand")]
    assert names == ["Linkerbot", "Mimic Robotics"]


def test_search_countries_aliases_and_year(tmp_path):
    conn = make_db(tmp_path)
    assert [s.name for s in db.search_startups(conn, countries=["USA"])] == ["Agility Robotics"]
    assert [s.name for s in db.search_startups(conn, countries=["China", "Switzerland"], founded_since=2020)] == ["Mimic Robotics"]
    assert db.search_startups(conn, countries=[" "]) != []  # empty country list means no country filter


def test_run_tool_handles_bad_arguments(tmp_path):
    conn = make_db(tmp_path)
    result, startups = agent.run_tool(conn, "search_startups", "not json")
    assert "error" in json.loads(result)
    assert startups == []


class FakeModel:
    """First answer: a tool call. Second answer: the final text."""

    def __init__(self, tool_arguments: str):
        self.requests = []
        self.answers = [
            SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(id="call_1", function=SimpleNamespace(name="search_startups", arguments=tool_arguments))],
            ),
            SimpleNamespace(content="Linkerbot (Beijing) builds hands. Source: https://a.com", tool_calls=None),
        ]
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.answers.pop(0))])


def test_ask_runs_tool_and_returns_answer_with_found_startups(tmp_path, monkeypatch):
    conn = make_db(tmp_path)
    fake = FakeModel('{"keyword": "robot hand", "countries": ["China"]}')
    monkeypatch.setattr(agent.llm, "client", lambda: fake)
    history = []

    answer = agent.ask("Which startups build robot hands in China?", history, conn)

    assert "Linkerbot" in answer.text
    assert [s.name for s in answer.startups] == ["Linkerbot"]
    tool_message = fake.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "Linkerbot" in tool_message["content"]
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_ask_retries_empty_responses(tmp_path, monkeypatch):
    fake = FakeModel('{"keyword": "robot hand"}')
    real_create = fake.create
    calls = {"n": 0}

    def sometimes_empty(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first response has no choices, like the free tier sometimes sends
            return SimpleNamespace(choices=None, model_extra={})
        return real_create(**kwargs)

    fake.chat.completions.create = sometimes_empty
    monkeypatch.setattr(agent.llm, "client", lambda: fake)
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    answer = agent.ask("Robot hands?", conn=make_db(tmp_path))
    assert "Linkerbot" in answer.text


def test_ask_reports_service_errors(tmp_path, monkeypatch):
    def broken():
        raise TimeoutError("slow")

    monkeypatch.setattr(agent.llm, "client", broken)
    answer = agent.ask("Hello?", conn=make_db(tmp_path))
    assert "did not answer" in answer.text
