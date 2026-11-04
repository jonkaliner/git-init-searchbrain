import tavily

from searchbrain import collect, db, programs, search
from searchbrain.models import Startup


def test_program_list_is_loaded_in_english():
    program_list = programs.load_programs()
    assert len(program_list) == 100
    hax = next(p for p in program_list if p.name == "HAX")
    assert (hax.region, hax.focus, hax.website) == ("North America", "Hardware; Robotics", "hax.co")
    assert not any("Nordamerika" in p.region or "Robotik" in p.focus for p in program_list)


def test_program_for_url_also_matches_subdomains():
    program_list = programs.load_programs()
    assert programs.domain_of("https://www.hax.co/portfolio") == "hax.co"
    assert programs.program_for_url("https://www.hax.co/portfolio", program_list) == "HAX"
    assert programs.program_for_url("https://blog.massrobotics.org/startups", program_list) == "MassRobotics"
    assert programs.program_for_url("https://techcrunch.com/article", program_list) is None


def test_join_names_without_duplicates():
    assert programs.join_names("Y Combinator", "HAX; Y Combinator", None) == "Y Combinator; HAX"
    assert programs.join_names(None, "") is None


def test_tavily_search_passes_domains_and_caches_them_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(search.config, "SEARCH_PROVIDER", "tavily")
    calls = []

    class FakeTavily:
        def __init__(self, api_key):
            pass

        def search(self, query, **options):
            calls.append(options)
            return {"results": [{"title": "HAX portfolio", "url": "https://hax.co/portfolio", "content": "Chips", "score": 0.9}]}

    monkeypatch.setattr(tavily, "TavilyClient", FakeTavily)
    monkeypatch.setattr(search.config, "get", lambda name: "test-key")

    search.search(["ai chip startup"], include_domains=["hax.co"])
    search.search(["ai chip startup"], include_domains=["hax.co"])  # from the cache
    search.search(["ai chip startup"])  # same words, but not limited: a separate search

    assert calls == [{"max_results": 10, "include_domains": ["hax.co"]}, {"max_results": 10}]


def test_collect_reads_accelerator_posts_and_marks_where_startups_were_found(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    conn = db.connect(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO feed_entries (id, program, title, link, summary, published, fetched_at) VALUES ('1', 'HAX', 'AI chip cohort', 'https://hax.co/chips', 'ChipCo builds AI chips', '2026-11-01', 'now')"
    )
    conn.commit()
    extracted_from = []

    def no_web(*args, **kwargs):
        raise AssertionError("the web must not be searched")

    def fake_extract(hits, topic=None, on_progress=None):
        extracted_from.extend(hit.url for hit in hits)
        return [Startup(name="ChipCo", description="AI chips for cars", source_url="https://hax.co/chips", topic=topic)]

    monkeypatch.setattr(collect, "search", no_web)
    monkeypatch.setattr(collect, "extract_startups", fake_extract)
    monkeypatch.setattr(collect, "geocode_all", lambda conn, startups: 0)
    monkeypatch.setattr(collect, "summarize", lambda startups: None)
    monkeypatch.setattr(collect, "save_map", lambda startups: tmp_path / "map.html")

    report = collect.collect("AI chips", sources=("feeds",))

    assert extracted_from == ["https://hax.co/chips"] and report["from_programs"] == 1
    assert db.all_startups(db.connect())[0].backed_by == "HAX"


def test_old_yc_rows_get_backed_by(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    conn.execute("INSERT INTO startups (key, name, yc_batch, created_at, updated_at) VALUES ('nori', 'Nori', 'S26', 'now', 'now')")
    conn.commit()
    assert db.all_startups(db.connect(tmp_path / "test.db"))[0].backed_by == "Y Combinator"
