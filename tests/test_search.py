import sys
import types

import pytest

from searchbrain import search


class FakeDDGSException(Exception):
    pass


def install_fake_ddgs(monkeypatch, answers):
    """Replace the ddgs library. `answers` is a list: each call takes the next item (a result list or an exception)."""
    calls = []

    class FakeDDGS:
        def __init__(self, timeout=None):
            pass

        def text(self, query, max_results=10, backend="auto"):
            calls.append({"query": query, "max_results": max_results, "backend": backend})
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    module = types.ModuleType("ddgs")
    module.DDGS = FakeDDGS
    exceptions = types.ModuleType("ddgs.exceptions")
    exceptions.DDGSException = FakeDDGSException
    module.exceptions = exceptions
    monkeypatch.setitem(sys.modules, "ddgs", module)
    monkeypatch.setitem(sys.modules, "ddgs.exceptions", exceptions)
    return calls


def install_fake_tavily(monkeypatch, urls):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(search.config, "get", lambda name: "test-key")
    monkeypatch.setitem(
        sys.modules,
        "tavily",
        types.SimpleNamespace(
            TavilyClient=lambda api_key: types.SimpleNamespace(
                search=lambda query, **options: {"results": [{"title": "T", "url": url, "content": "c", "score": 0.7} for url in urls]}
            )
        ),
    )


@pytest.fixture(autouse=True)
def isolated_search(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(search.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(search.config, "SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.setattr(search, "_ddg_failures", 0)
    monkeypatch.setattr(search, "_ddg_resting_until", 0.0)
    monkeypatch.setattr(search, "_ddg_last_request", 0.0)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


def test_duckduckgo_results_become_hits_and_are_cached(monkeypatch):
    calls = install_fake_ddgs(
        monkeypatch,
        [[{"title": "Mimic", "href": "https://a.com", "body": "AI robotic hand"}, {"title": "Proception", "href": "https://b.com", "body": "Hands"}]],
    )

    first = search.search(["robot hand startup"])
    second = search.search(["robot hand startup"])  # from the cache: no second request

    assert [h.url for h in first] == ["https://a.com", "https://b.com"]
    assert first[0].score > first[1].score and first[0].content == "AI robotic hand"
    assert [h.url for h in second] == ["https://a.com", "https://b.com"]
    assert len(calls) == 1 and calls[0]["backend"] == "duckduckgo"  # only DuckDuckGo, not the other engines of the library


def test_domain_limit_uses_site_operator(monkeypatch):
    calls = install_fake_ddgs(monkeypatch, [[{"title": "HAX", "href": "https://hax.co/p", "body": "x"}]])
    search.search(["ai chip startup"], include_domains=["hax.co", "seedcamp.com"])
    assert "(site:hax.co OR site:seedcamp.com)" in calls[0]["query"]


def test_results_without_address_are_thrown_away_and_not_cached(monkeypatch):
    # DuckDuckGo's block page shows up as one "result" without a link
    calls = install_fake_ddgs(monkeypatch, [[{"title": "", "href": "", "body": ""}]] * 2 + [[{"title": "T", "href": "https://a.com", "body": "b"}]])

    assert search.search(["lidar startup"]) == []
    assert len(calls) == 2  # DDG_ATTEMPTS
    assert [h.url for h in search.search(["lidar startup"])] == ["https://a.com"]  # not cached as empty, so asked again


def test_refusals_are_survived_and_retried_next_time(monkeypatch):
    calls = install_fake_ddgs(monkeypatch, [FakeDDGSException("No results found.")] * 2 + [[{"title": "T", "href": "https://a.com", "body": "b"}]])

    assert search.search(["lidar startup"]) == []  # refused twice: no crash, just no hits
    assert len(search.search(["lidar startup"])) == 1
    assert len(calls) == 3


def test_duckduckgo_rests_after_repeated_failures(monkeypatch):
    calls = install_fake_ddgs(monkeypatch, [FakeDDGSException("No results found.")] * 20)

    for number in range(5):
        search.search([f"query {number}"])

    assert len(calls) == search.DDG_MAX_FAILURES * search.DDG_ATTEMPTS  # after 3 failed queries it stopped asking


def test_tavily_is_the_backup_when_duckduckgo_gives_nothing(monkeypatch):
    install_fake_ddgs(monkeypatch, [FakeDDGSException("blocked")] * 2)
    install_fake_tavily(monkeypatch, ["https://t.com"])
    assert [h.url for h in search.search(["lidar startup"])] == ["https://t.com"]


def test_requests_are_paced(monkeypatch):
    install_fake_ddgs(monkeypatch, [[{"title": "T", "href": "https://a.com", "body": "b"}]] * 2)
    pauses = []
    monkeypatch.setattr(search.time, "sleep", pauses.append)
    search.search(["first query"])
    search.search(["second query"])
    assert pauses and all(0 < pause <= search.DDG_PAUSE for pause in pauses)


def test_cache_keys_keep_old_tavily_entries_valid():
    old_style = search._cache_key("Lidar startup", 10)
    assert old_style == search._cache_key("lidar startup ", 10, None, "tavily")
    assert search._cache_key("lidar startup", 10, None, "duckduckgo") != old_style
