"""The tools of the Scout and Analyst agents, written as LangChain tools.

Every tool takes plain text and returns a short JSON text. Short answers keep the free NVIDIA model fast.
Tools never raise: a problem comes back as a sentence the agent can read and work around.

Everything a tool returns is remembered for the current run. The crew only accepts a startup name,
website or source url that literally appeared in this text, so an agent cannot make something up.
"""

import json
import threading

from langchain_core.tools import tool

from searchbrain import db, feeds, portfolio, search, web
from searchbrain.log import get_logger
from searchbrain.models import Startup

logger = get_logger(__name__)

MAX_STARTUPS = 12
MAX_POSTS = 8
MAX_WEB_HITS = 8
MAX_REMEMBERED = 3_000_000  # characters, just a safety limit

_seen: list[str] = []
_lock = threading.Lock()
_progress = None  # optional function that gets one line per tool call, e.g. print


def set_progress(callback) -> None:
    global _progress
    _progress = callback


def _note(tool_name: str, argument: str) -> None:
    """Log a tool call (and show it, if someone listens), so a run can be followed afterwards."""
    line = f"{tool_name}({argument[:80]})"
    logger.info("Tool call: %s", line)
    if _progress:
        _progress(line)


def reset_seen() -> None:
    """Forget what the tools returned. Call this at the start of every run."""
    with _lock:
        _seen.clear()


def seen_text() -> str:
    """Everything the tools returned in this run, in lower case."""
    with _lock:
        return "\n".join(_seen).lower()


def _return(text: str) -> str:
    """Remember a tool result, then hand it to the agent."""
    with _lock:
        if sum(map(len, _seen)) < MAX_REMEMBERED:
            _seen.append(text)
    return text


def _compact(values: dict) -> dict:
    return {key: value for key, value in values.items() if value not in (None, "")}


def startup_row(s: Startup) -> dict:
    return _compact(
        {
            "name": s.name, "what": s.description, "industry": s.industry, "country": s.country, "city": s.city,
            "founded": s.founding_year, "website": s.link, "yc_batch": s.yc_batch, "program": s.backed_by,
        }
    )


@tool
def search_known_startups(keywords: str, countries: str = "") -> str:
    """Search the startups already in the database (all active Y Combinator companies and earlier finds).
    keywords: two to four topic words, e.g. "robot hand". A startup needs at least half of them; more matching words rank higher.
    countries: optional, comma separated, e.g. "Germany, France".
    Returns up to 12 startups, best match first. Try several different keyword combinations."""
    _note("search_known_startups", f"{keywords!r}, countries={countries!r}" if countries else repr(keywords))
    conn = db.connect()
    try:
        best = db.search_ranked(conn, keywords, countries=[c for c in countries.split(",") if c.strip()], limit=MAX_STARTUPS)
    finally:
        conn.close()
    if not best:
        return _return("No startup in the database matches these words. Try different or more general words.")
    return _return(json.dumps([startup_row(s) for s in best], ensure_ascii=False))


@tool
def search_program_news(keywords: str) -> str:
    """Search the latest posts of accelerators and incubators (RSS feeds): new cohorts, demo days, portfolio news.
    keywords: words that must ALL appear in the post, e.g. "robotics cohort".
    Returns up to 8 posts, newest first. A post often names several startups: use read_page to see them."""
    _note("search_program_news", repr(keywords))
    conn = db.connect()
    try:
        posts = feeds.search_entries(conn, keywords, limit=MAX_POSTS)
    finally:
        conn.close()
    if not posts:
        return _return("No recent accelerator post matches these words. Try fewer or more general words.")
    return _return(
        json.dumps(
            [_compact({"program": p["program"], "title": p["title"], "date": p["published"], "url": p["link"], "text": (p["summary"] or "")[:300]}) for p in posts],
            ensure_ascii=False,
        )
    )


@tool
def search_portfolios(keywords: str) -> str:
    """Search the portfolio pages of about 70 accelerators and incubators (the pages that list their startups).
    keywords: two to four topic words, e.g. "robotics hardware". A page needs at least half of them.
    Returns up to 6 pages with the text around the match. The startups named there are candidates: copy
    their names exactly. Use read_page on the page address to see more."""
    _note("search_portfolios", repr(keywords))
    conn = db.connect()
    try:
        pages = portfolio.search_pages(conn, keywords, limit=6)
    finally:
        conn.close()
    if not pages:
        return _return("No accelerator portfolio page matches these words. Try different or more general words.")
    return _return(json.dumps([{"program": p["program"], "url": p["url"], "text": p["text"]} for p in pages], ensure_ascii=False))


@tool
def search_web(query: str) -> str:
    """Search the web (DuckDuckGo). Use it for startups the database and the accelerator posts do not show.
    query: a normal search query, e.g. "dexterous robot hand startup seed funding".
    Returns up to 8 results with a short text each."""
    _note("search_web", repr(query))
    hits = search.search([query], max_results=MAX_WEB_HITS)[:MAX_WEB_HITS]
    if not hits:
        return _return("The web search gave no results (it may be rate limited). Continue with what you have.")
    return _return(json.dumps([{"title": h.title, "url": h.url, "text": h.content[:300]} for h in hits], ensure_ascii=False))


@tool
def read_page(url: str) -> str:
    """Read the text of one web page (about 3500 characters). Use it only for a promising page, e.g. an
    accelerator post that lists startups, or a company website to check where it is based.
    url: the full address, taken from an earlier tool result."""
    _note("read_page", url)
    return _return(f"{url}\n{web.page_text(url, limit=3500)}")


SCOUT_TOOLS = [search_known_startups, search_program_news, search_portfolios, search_web, read_page]
ANALYST_TOOLS = [search_web, read_page]
