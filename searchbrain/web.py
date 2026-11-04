"""Polite web access for feeds and pages.

- robots.txt is checked before every request; a site that forbids our bot is not read
- we say who we are in the user agent
- only public http(s) addresses: no localhost or private networks (also after redirects)
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from searchbrain import config
from searchbrain.log import get_logger

logger = get_logger(__name__)

TIMEOUT = 15
MAX_BYTES = 2_000_000  # bigger pages are cut off, we only need the beginning
MAX_REDIRECTS = 4
HEADERS = {"User-Agent": config.BOT_USER_AGENT}
NOISE_TAGS = ["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe"]

_robots: dict[str, RobotFileParser | None] = {}  # one robots.txt per site, read once per run


class NotAllowed(Exception):
    """The page must not be read (robots.txt, private address or wrong scheme)."""


def is_public(url: str) -> bool:
    """True for http(s) urls whose host is not a private, loopback or link-local address."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, None)}
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(address).is_global for address in addresses)


def _robots_for(site: str) -> RobotFileParser | None:
    """The parsed robots.txt of a site ("https://example.com"), or None if the site cannot be reached."""
    if site not in _robots:
        parser = RobotFileParser()
        try:
            response = requests.get(f"{site}/robots.txt", headers=HEADERS, timeout=TIMEOUT)
            if response.status_code in (401, 403) or response.status_code >= 500:
                parser.disallow_all = True  # forbidden or broken: be careful and stay away
            elif response.status_code >= 400:
                parser.allow_all = True  # no robots.txt means everything is allowed
            else:
                parser.parse(response.text.splitlines())
        except requests.RequestException:
            parser = None  # not reachable: the page request will fail the same way
        _robots[site] = parser
    return _robots[site]


def allowed(url: str) -> bool:
    """True if robots.txt allows our bot to fetch this url."""
    parts = urlparse(url)
    parser = _robots_for(f"{parts.scheme}://{parts.netloc}")
    return True if parser is None else parser.can_fetch(config.BOT_NAME, url)


def get(url: str) -> bytes:
    """Fetch a url politely and return its body (cut off at MAX_BYTES).

    Raises NotAllowed, or requests.RequestException when the site does not answer properly.
    Redirects are followed by hand so that every step is checked.
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public(url):
            raise NotAllowed("not a public http(s) address")
        if not allowed(url):
            raise NotAllowed("robots.txt of the site does not allow this")
        with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False) as response:
            if response.is_redirect and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            body = b""
            for chunk in response.iter_content(65536):
                body += chunk
                if len(body) >= MAX_BYTES:
                    break
            return body
    raise NotAllowed("too many redirects")


def html_to_text(html: str | bytes, limit: int | None = None) -> str:
    """Readable text of an HTML snippet or page: no scripts, menus or footers, whitespace collapsed."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return text[:limit] if limit else text


def page_text(url: str, limit: int = 4000) -> str:
    """The text of a web page (title first), or a short sentence that says why it could not be read."""
    try:
        body = get(url)
    except NotAllowed as error:
        return f"Cannot read {url}: {error}."
    except requests.RequestException as error:
        logger.warning("Could not read %s: %s", url, error)
        return f"Cannot read {url}: {type(error).__name__}."
    title = BeautifulSoup(body, "html.parser").title
    return f"{title.get_text(strip=True) if title else ''}\n{html_to_text(body, limit)}".strip()
