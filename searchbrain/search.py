"""Step 3: web search with DuckDuckGo (no key needed), with a local JSON cache.

DuckDuckGo has no official search API. The library asks its web page, and after a few quick
requests DuckDuckGo answers with "No results found" or a page without results. So:
- requests are paced (DDG_PAUSE seconds apart)
- results without an address are thrown away and never cached
- after DDG_MAX_FAILURES refusals in a row DuckDuckGo is left alone for DDG_COOLDOWN seconds
Tavily is the backup: set SEARCH_PROVIDER=tavily, or add TAVILY_API_KEY and it is used whenever
DuckDuckGo gives nothing. The agents also work without any web search (database + accelerator posts).

Try it with:  .venv313\\Scripts\\python.exe -m searchbrain.search "dexterous hand" --industry robotics
"""

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from searchbrain import config
from searchbrain.log import get_logger

logger = get_logger(__name__)

CACHE_FILE = Path("data") / "search_cache.json"
DDG_PAUSE = 6.0  # seconds between DuckDuckGo requests
DDG_ATTEMPTS = 2  # per query
DDG_MAX_FAILURES = 3  # queries in a row without an answer, then DuckDuckGo gets a rest
DDG_COOLDOWN = 600  # seconds

_ddg_last_request = 0.0
_ddg_failures = 0
_ddg_resting_until = 0.0


@dataclass
class SearchHit:
    title: str
    url: str
    content: str
    score: float


def build_queries(keyword: str = "", industry: str = "", country: str = "", since_year: int | None = None) -> list[str]:
    """Turn the filter inputs into a few search queries."""
    topic = " ".join(part for part in [keyword, industry] if part).strip() or "young"
    place = f" {country}" if country else ""
    founded = f" founded {since_year} or later" if since_year else ""
    return [
        f"{topic} startup{place}{founded}",
        f"top {topic} startups{place} list",
        f"{topic} company raised seed funding{place}",
    ]


def _cache_key(query: str, max_results: int, include_domains: list[str] | None = None, provider: str = "tavily") -> str:
    text = f"{query.lower().strip()}|{max_results}"
    if include_domains:  # only then, so cache entries from before keep their keys
        text += "|" + ",".join(sorted(include_domains))
    if provider != "tavily":  # Tavily was the first provider: its cache entries keep their keys
        text += f"|{provider}"
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _pace() -> None:
    """Wait until DDG_PAUSE seconds have passed since the last DuckDuckGo request."""
    global _ddg_last_request
    wait = DDG_PAUSE - (time.monotonic() - _ddg_last_request)
    if wait > 0:
        time.sleep(wait)
    _ddg_last_request = time.monotonic()


def _duckduckgo(query: str, max_results: int, include_domains: list[str] | None) -> list[dict]:
    """Ask DuckDuckGo. Returns [] when it has nothing to say or refuses (that is counted, see DDG_MAX_FAILURES)."""
    global _ddg_failures, _ddg_resting_until
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    if time.monotonic() < _ddg_resting_until:
        return []
    if include_domains:  # DuckDuckGo has no domain list option: use the site: operator
        query += " (" + " OR ".join(f"site:{domain}" for domain in include_domains) + ")"

    for attempt in range(1, DDG_ATTEMPTS + 1):
        _pace()
        try:
            found = [item for item in DDGS(timeout=20).text(query, max_results=max_results, backend="duckduckgo") if item.get("href")]
        except DDGSException as error:
            logger.warning("DuckDuckGo attempt %s/%s for %r: %s", attempt, DDG_ATTEMPTS, query, error)
            continue
        if found:
            _ddg_failures = 0
            # DuckDuckGo gives no relevance score: the order of the answer is the score
            return [
                {"url": item["href"], "title": item.get("title", ""), "content": item.get("body", ""), "score": 1 - index / len(found)}
                for index, item in enumerate(found)
            ]
        logger.warning("DuckDuckGo attempt %s/%s for %r: no usable results", attempt, DDG_ATTEMPTS, query)

    _ddg_failures += 1
    if _ddg_failures >= DDG_MAX_FAILURES:
        _ddg_resting_until = time.monotonic() + DDG_COOLDOWN
        logger.error("DuckDuckGo gave nothing for %s queries in a row: not asking again for %s minutes", _ddg_failures, DDG_COOLDOWN // 60)
        _ddg_failures = 0
    return []


def _tavily(query: str, max_results: int, include_domains: list[str] | None) -> list[dict]:
    from tavily import TavilyClient

    options = {"include_domains": include_domains} if include_domains else {}
    response = TavilyClient(api_key=config.get("TAVILY_API_KEY")).search(query, max_results=max_results, **options)
    return response.get("results", [])


def _run_query(query: str, max_results: int, include_domains: list[str] | None) -> list[dict]:
    """Run one query on the configured provider. Search errors are logged, not raised: the result is then empty."""
    if config.SEARCH_PROVIDER == "tavily":
        return _tavily(query, max_results, include_domains)
    try:
        results = _duckduckgo(query, max_results, include_domains)
    except Exception as error:  # the network is down, or the library broke
        logger.error("DuckDuckGo search failed for %r: %s: %s", query, type(error).__name__, error)
        results = []
    if not results and os.getenv("TAVILY_API_KEY", "").strip():
        logger.info("Using Tavily as the backup for %r", query)
        try:
            return _tavily(query, max_results, include_domains)
        except Exception as error:
            logger.error("Tavily search failed for %r: %s: %s", query, type(error).__name__, error)
    return results


def search(queries: list[str], max_results: int = 10, include_domains: list[str] | None = None) -> list[SearchHit]:
    """Run each query (or read it from the cache) and return unique hits, best first.

    include_domains limits the search to these websites. A query that fails is skipped and
    not cached, so the next run tries it again.
    """
    cache = _load_cache()
    hits: dict[str, SearchHit] = {}

    for query in queries:
        key = _cache_key(query, max_results, include_domains, config.SEARCH_PROVIDER)
        if key not in cache:
            results = _run_query(query, max_results, include_domains)
            if not results:
                continue
            cache[key] = {"query": query, "results": results}
            _save_cache(cache)

        for result in cache[key]["results"]:
            url = result.get("url", "")
            if url and url not in hits:
                hits[url] = SearchHit(
                    title=result.get("title", ""),
                    url=url,
                    content=result.get("content", ""),
                    score=float(result.get("score") or 0),
                )

    return sorted(hits.values(), key=lambda hit: hit.score, reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the web for startups (raw hits).")
    parser.add_argument("keyword", nargs="?", default="")
    parser.add_argument("--industry", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--since", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="print hits as JSON")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # the Windows console cannot print every character of a web page

    queries = build_queries(args.keyword, args.industry, args.country, args.since)
    hits = search(queries)

    if args.json:
        print(json.dumps([asdict(hit) for hit in hits], indent=2, ensure_ascii=False))
        return

    print(f"Queries: {queries}\n{len(hits)} unique hits\n")
    for hit in hits:
        print(f"[{hit.score:.2f}] {hit.title}\n       {hit.url}\n       {hit.content[:200]}\n")


if __name__ == "__main__":
    main()
