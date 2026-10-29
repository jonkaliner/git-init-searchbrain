"""Step 3: web search with Tavily, with a local JSON cache.

Try it with:  .venv\\Scripts\\python.exe -m searchbrain.search "dexterous hand" --industry robotics
"""

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from searchbrain import config

CACHE_FILE = Path("data") / "search_cache.json"


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


def _cache_key(query: str, max_results: int) -> str:
    return hashlib.sha256(f"{query.lower().strip()}|{max_results}".encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def search(queries: list[str], max_results: int = 10) -> list[SearchHit]:
    """Run each query on Tavily (or read it from the cache) and return unique hits."""
    cache = _load_cache()
    client = None
    hits: dict[str, SearchHit] = {}

    for query in queries:
        key = _cache_key(query, max_results)
        if key not in cache:
            if client is None:
                from tavily import TavilyClient

                client = TavilyClient(api_key=config.get("TAVILY_API_KEY"))
            response = client.search(query, max_results=max_results)
            cache[key] = {"query": query, "results": response.get("results", [])}
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
