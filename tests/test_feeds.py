from searchbrain import db, feeds, programs, web

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>HAX blog</title>
<item><title>Meet our new cohort</title><link>https://hax.co/blog/cohort</link>
<description>&lt;p&gt;Ten &lt;b&gt;hardware&lt;/b&gt; startups join, including a robot hand company.&lt;/p&gt;</description>
<pubDate>Mon, 03 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Demo day recap</title><link>https://hax.co/blog/demo-day</link><description>Chips and lidar.</description></item>
<item><title></title><link>https://hax.co/blog/broken</link></item>
</channel></rss>"""

HOME = b'<html><head><link rel="alternate" type="application/rss+xml" href="/blog/rss.xml"></head><body>Hi</body></html>'


def test_parse_feed_cleans_html_and_skips_broken_posts():
    entries = feeds.parse_feed(RSS)
    assert [e["title"] for e in entries] == ["Meet our new cohort", "Demo day recap"]
    assert entries[0]["summary"] == "Ten hardware startups join, including a robot hand company."
    assert entries[0]["published"] == "2026-11-03" and entries[1]["published"] is None
    assert feeds.parse_feed(b"<html><body>not a feed</body></html>") == []


def test_find_feed_prefers_the_link_on_the_homepage(monkeypatch):
    pages = {"https://hax.co/": HOME, "https://hax.co/blog/rss.xml": RSS}

    def fake_get(url):
        if url in pages:
            return pages[url]
        raise web.requests.HTTPError("404")

    monkeypatch.setattr(feeds.web, "get", fake_get)
    assert feeds.find_feed("hax.co") == "https://hax.co/blog/rss.xml"


def test_find_feed_tries_common_paths_and_gives_up(monkeypatch):
    pages = {"https://hax.co/": b"<html><body>No link</body></html>", "https://hax.co/feed": RSS}

    def fake_get(url):
        if url in pages:
            return pages[url]
        raise web.requests.HTTPError("404")

    monkeypatch.setattr(feeds.web, "get", fake_get)
    assert feeds.find_feed("hax.co") == "https://hax.co/feed"

    monkeypatch.setattr(feeds.web, "get", lambda url: b"<html>nothing</html>")
    assert feeds.find_feed("hax.co") is None


def test_discover_saves_programs_without_feed_and_does_not_ask_again(tmp_path):
    program_list = [
        programs.Program("HAX", "Accelerator", "Newark", "US", "North America", "Hardware", "hax.co"),
        programs.Program("Seedcamp", "VC", "London", "UK", "Europe", "General", "seedcamp.com"),
    ]
    path = tmp_path / "feeds.csv"
    asked = []

    def finder(website):
        asked.append(website)
        return "https://hax.co/feed" if website == "hax.co" else None

    rows = feeds.discover(program_list, path, finder=finder)
    assert [(r["program"], r["feed_url"]) for r in rows] == [("HAX", "https://hax.co/feed"), ("Seedcamp", "")]

    feeds.discover(program_list, path, finder=finder)
    assert asked == ["hax.co", "seedcamp.com"]  # the second run found everything checked already


def test_refresh_stores_new_posts_once_and_survives_a_broken_feed(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    rows = [
        {"program": "HAX", "website": "hax.co", "feed_url": "https://hax.co/feed", "checked_at": ""},
        {"program": "Broken", "website": "broken.com", "feed_url": "https://broken.com/feed", "checked_at": ""},
    ]

    def fetch(url):
        if "broken" in url:
            raise web.requests.ConnectionError("down")
        return RSS

    first = feeds.refresh(conn, rows, fetch=fetch)
    second = feeds.refresh(conn, rows, fetch=fetch)

    assert (first["new_posts"], first["failed"]) == (2, 1)
    assert second["new_posts"] == 0  # the same posts are not stored twice


def test_search_entries_finds_plural_words_newest_first(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    feeds.refresh(conn, [{"program": "HAX", "website": "hax.co", "feed_url": "x", "checked_at": ""}], fetch=lambda url: RSS)

    found = feeds.search_entries(conn, "robot hands")
    assert [e["title"] for e in found] == ["Meet our new cohort"]
    assert found[0]["program"] == "HAX"
    assert feeds.search_entries(conn, "biotech") == []
