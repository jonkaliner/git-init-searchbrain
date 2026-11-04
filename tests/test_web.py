import pytest
import requests

from searchbrain import web


class FakeResponse:
    def __init__(self, status=200, text="", body=b"", headers=None):
        self.status_code = status
        self.text = text
        self._body = body or text.encode()
        self.headers = headers or {}
        self.is_redirect = status in (301, 302) and "Location" in self.headers

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def reset():
    web._robots.clear()


def serve(monkeypatch, pages):
    """Fake the internet: pages maps a url to a FakeResponse. Unknown urls are 404."""
    monkeypatch.setattr(web, "is_public", lambda url: url.startswith("http"))  # no real DNS lookups in these tests
    requested = []

    def fake_get(url, **options):
        requested.append(url)
        return pages.get(url, FakeResponse(404))

    monkeypatch.setattr(web.requests, "get", fake_get)
    return requested


def test_robots_txt_blocks_a_path(monkeypatch):
    serve(
        monkeypatch,
        {
            "https://a.com/robots.txt": FakeResponse(text="User-agent: *\nDisallow: /private"),
            "https://a.com/public": FakeResponse(text="<html><title>Hi</title><body>Hello</body></html>"),
        },
    )
    assert web.allowed("https://a.com/public")
    assert not web.allowed("https://a.com/private/page")
    assert "robots.txt" in web.page_text("https://a.com/private/page")


def test_missing_robots_txt_allows_but_forbidden_robots_txt_blocks(monkeypatch):
    serve(monkeypatch, {"https://open.com/robots.txt": FakeResponse(404), "https://shut.com/robots.txt": FakeResponse(403)})
    assert web.allowed("https://open.com/x")
    assert not web.allowed("https://shut.com/x")


def test_our_bot_name_is_matched_in_robots_txt(monkeypatch):
    serve(monkeypatch, {"https://a.com/robots.txt": FakeResponse(text="User-agent: SearchbrainBot\nDisallow: /")})
    assert not web.allowed("https://a.com/anything")


def test_page_text_has_title_and_no_menus_or_scripts(monkeypatch):
    html = "<html><title>HAX</title><nav>Menu</nav><script>var x=1</script><body><p>We build   hardware.</p><footer>Legal</footer></body></html>"
    serve(monkeypatch, {"https://hax.co/robots.txt": FakeResponse(404), "https://hax.co/": FakeResponse(text=html)})
    text = web.page_text("https://hax.co/")
    assert text.startswith("HAX") and "We build hardware." in text
    assert "Menu" not in text and "var x" not in text and "Legal" not in text


def test_redirects_are_checked_step_by_step(monkeypatch):
    serve(
        monkeypatch,
        {
            "https://a.com/robots.txt": FakeResponse(404),
            "https://a.com/go": FakeResponse(302, headers={"Location": "http://localhost/secret"}),
        },
    )
    monkeypatch.setattr(web, "is_public", lambda url: "localhost" not in url)
    assert "not a public" in web.page_text("https://a.com/go")


def test_private_addresses_are_refused():
    assert not web.is_public("ftp://example.com/file")
    assert not web.is_public("http://127.0.0.1/admin") and not web.is_public("http://localhost:8501")
