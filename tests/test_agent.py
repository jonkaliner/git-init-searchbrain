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


def as_stream(message):
    """Send a model answer in pieces, like the streamed API: tool call arguments arrive in two parts."""
    first, second = [], []
    for i, call in enumerate(message.tool_calls or []):
        half = len(call.function.arguments) // 2
        first.append(SimpleNamespace(index=i, id=call.id, function=SimpleNamespace(name=call.function.name, arguments=call.function.arguments[:half])))
        second.append(SimpleNamespace(index=i, id=None, function=SimpleNamespace(name=None, arguments=call.function.arguments[half:])))
    return iter(
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=message.content, tool_calls=first or None))]),
            SimpleNamespace(choices=[]),  # the API also sends chunks without choices
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=second or None))]),
        ]
    )


def tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeModel:
    """First answer: a tool call. Second answer: the final text."""

    def __init__(self, tool_arguments: str):
        self.requests = []
        self.answers = [
            SimpleNamespace(content=None, tool_calls=[tool_call("call_1", "search_startups", tool_arguments)]),
            SimpleNamespace(content="Linkerbot (Beijing) builds hands. Source: https://a.com", tool_calls=None),
        ]
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return as_stream(self.answers.pop(0))


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


def test_ask_returns_web_search_proposal(tmp_path, monkeypatch):
    fake = FakeModel('{"keyword": "lidar"}')
    fake.answers[0].tool_calls.append(
        tool_call("call_2", "propose_web_search", '{"topic": "lidar sensors", "subtopics": ["automotive lidar"]}')
    )
    fake.answers[1].content = "The database has no lidar startups yet. You can start a web search below."
    monkeypatch.setattr(agent.llm, "client", lambda: fake)

    answer = agent.ask("I am searching for lidar companies", conn=make_db(tmp_path))

    assert answer.web_search == {"topic": "lidar sensors", "subtopics": ["automotive lidar"]}
    assert "NOT run" in fake.requests[1]["messages"][-1]["content"]  # the model is told the search did not run


def test_parse_proposal_checks_arguments():
    assert agent.parse_proposal('{"topic": " lidar ", "subtopics": ["a", "b", "c", "d", ""]}') == {"topic": "lidar", "subtopics": ["a", "b", "c"]}
    assert agent.parse_proposal('{"topic": ""}') is None
    assert agent.parse_proposal("not json") is None


def test_search_finds_singular_for_plural_words(tmp_path):
    conn = make_db(tmp_path)
    assert [s.name for s in db.search_startups(conn, keyword="robotic hands")] == ["Linkerbot", "Mimic Robotics"]
    assert db.keyword_stems("lidar sensors glass") == ["lidar", "sensor", "glass"]


def test_ask_retries_empty_responses(tmp_path, monkeypatch):
    fake = FakeModel('{"keyword": "robot hand"}')
    real_create = fake.create
    calls = {"n": 0}

    def sometimes_empty(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # first response has no choices, like the free tier sometimes sends
            return iter([SimpleNamespace(choices=[])])
        return real_create(**kwargs)

    fake.chat.completions.create = sometimes_empty
    monkeypatch.setattr(agent.llm, "client", lambda: fake)
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    answer = agent.ask("Robot hands?", conn=make_db(tmp_path))
    assert "Linkerbot" in answer.text


def test_ask_with_show_matches_needs_no_third_model_call(tmp_path, monkeypatch):
    fake = FakeModel('{"keyword": "robot"}')
    fake.answers[1] = SimpleNamespace(
        content=None,
        tool_calls=[
            tool_call(
                "call_2",
                "show_matches",
                '{"matches": [{"name": "Mimic Robotics", "score": 5, "why": "AI robotic hand."},'
                ' {"name": "Made Up Robots", "score": 4, "why": "not in the results"},'
                ' {"name": "Linkerbot", "score": 9, "why": "Dexterous hands."}]}',
            )
        ],
    )
    monkeypatch.setattr(agent.llm, "client", lambda: fake)
    steps = []

    answer = agent.ask("Robot hand startups?", conn=make_db(tmp_path), on_step=steps.append)

    assert [(m["startup"].name, m["score"]) for m in answer.matches] == [("Mimic Robotics", 5), ("Linkerbot", 5)]  # made-up name dropped, score capped
    assert answer.text == "I found 2 potential matches. The best fits are Mimic Robotics and Linkerbot."
    assert len(fake.requests) == 2  # search + show_matches, no extra call for the final text
    assert fake.requests[0]["stream"] is True
    assert steps[0] == "Understanding the question …" and "ranking" in steps[1]


def test_endless_searching_ends_with_a_deal_flow_card(tmp_path, monkeypatch):
    fake = FakeModel('{"keyword": "autonomous driving AI chip"}')
    fake.answers = [
        SimpleNamespace(content=None, tool_calls=[tool_call(f"call_{i}", "search_startups", f'{{"keyword": "{word}"}}')])
        for i, word in enumerate(["autonomous driving AI chip", "AI chip", "autonomous driving", "chip", "AI chip"])
    ]
    monkeypatch.setattr(agent.llm, "client", lambda: fake)
    steps = []

    answer = agent.ask("Build a deal flow for autonomous driving AI chip startups", conn=make_db(tmp_path), on_step=steps.append)

    assert answer.web_search == {"topic": "autonomous driving AI chip", "subtopics": ["AI chip", "autonomous driving"]}
    assert "deal flow" in answer.text
    assert "Search limit reached" in fake.requests[-1]["messages"][-1]["content"]  # 4th and 5th search were refused
    assert "Nothing fitting yet" in steps[1]


def test_topic_from_question():
    question = "Build a deal flow for autonomous driving AI chip startups, early stage, Europe or US"
    assert agent.topic_from_question(question) == "autonomous driving AI chip"
    assert agent.topic_from_question("Find startups in battery recycling") == "battery recycling"
    assert agent.topic_from_question("I am looking for companies related to lidar sensors") == "lidar sensors"
    assert agent.topic_from_question("Which startups are in Berlin?") is None  # a question, not a search request


def test_deal_flow_card_from_question_when_the_model_gives_nothing(tmp_path, monkeypatch):
    def empty_answers(**kwargs):
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="", tool_calls=None))])])

    monkeypatch.setattr(agent.llm, "client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=empty_answers))))
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)

    answer = agent.ask("Build a deal flow for autonomous driving AI chip startups", conn=make_db(tmp_path))
    assert answer.web_search == {"topic": "autonomous driving AI chip", "subtopics": []}
    assert "card below" in answer.text


def test_hanging_request_is_retried(tmp_path, monkeypatch):
    fake = FakeModel('{"keyword": "robot hand"}')
    real_create = fake.create
    calls = {"n": 0}

    def hangs_once(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("no answer within the stream timeout")
        return real_create(**kwargs)

    fake.chat.completions.create = hangs_once
    monkeypatch.setattr(agent.llm, "client", lambda: fake)
    assert "Linkerbot" in agent.ask("Robot hands?", conn=make_db(tmp_path)).text


def test_ask_reports_service_errors(tmp_path, monkeypatch):
    def broken():
        raise TimeoutError("slow")

    monkeypatch.setattr(agent.llm, "client", broken)
    answer = agent.ask("Hello?", conn=make_db(tmp_path))
    assert "did not answer" in answer.text
