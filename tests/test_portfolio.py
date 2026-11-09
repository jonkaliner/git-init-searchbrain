from searchbrain import db, portfolio, programs

HOME = b'<html><body><a href="/about">About</a><a href="/portfolio/">Our startups</a><a href="https://other.com/portfolio">x</a><a href="/blog">Blog</a></body></html>'
LONG = ("<html><body>" + "HAX builds robotics hardware startups. " * 20 + "</body></html>").encode()


def test_candidate_links_keep_only_startup_lists_of_the_same_site():
    assert portfolio.candidate_links(HOME, "https://hax.co/") == ["https://hax.co/portfolio/"]


def test_find_pages_needs_text_and_does_not_count_a_page_twice(monkeypatch):
    pages = {"https://hax.co/": HOME, "https://hax.co/portfolio/": LONG, "https://hax.co/portfolio": LONG, "https://hax.co/startups": b"<html>tiny</html>"}

    def fake_get(url):
        if url in pages:
            return pages[url]
        raise portfolio.requests.HTTPError("404")

    monkeypatch.setattr(portfolio.web, "get", fake_get)
    assert portfolio.find_pages("hax.co") == ["https://hax.co/portfolio/"]


def test_top_programs_skip_yc_and_follow_the_file_order():
    names = [p.name for p in portfolio.top_programs()]
    assert "Y Combinator" not in names and names[0] == "Techstars" and len(names) <= portfolio.TOP_PROGRAMS


def test_discover_saves_programs_without_page_and_does_not_ask_again(tmp_path):
    program_list = [programs.Program("HAX", "Accelerator", "Newark", "US", "North America", "Hardware", "hax.co"),
                    programs.Program("Seedcamp", "VC", "London", "UK", "Europe", "General", "seedcamp.com")]
    path, asked = tmp_path / "p.csv", []

    def finder(website):
        asked.append(website)
        return ["https://hax.co/portfolio"] if website == "hax.co" else []

    rows = portfolio.discover(program_list, path, finder=finder)
    assert [(r["program"], r["urls"]) for r in rows] == [("HAX", "https://hax.co/portfolio"), ("Seedcamp", "")]
    portfolio.discover(program_list, path, finder=finder)
    assert asked == ["hax.co", "seedcamp.com"]


def test_refresh_and_search_return_the_text_around_the_match(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    rows = [{"program": "HAX", "website": "hax.co", "urls": "https://hax.co/portfolio https://hax.co/down"}]

    def fetch(url):
        if url.endswith("down"):
            raise portfolio.requests.ConnectionError("down")
        return b"<html><body>" + b"filler " * 100 + b"Robo Hands builds dexterous robot hands. " + b"more " * 100 + b"</body></html>"

    report = portfolio.refresh(conn, rows, fetch=fetch)
    assert (report["pages"], report["failed"]) == (1, 1)
    hit = portfolio.search_pages(conn, "robot hands")[0]
    assert hit["program"] == "HAX" and "Robo Hands builds dexterous robot hands" in hit["text"]
    assert portfolio.search_pages(conn, "biotech chip") == []
