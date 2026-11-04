"""Step 5: let the NVIDIA model turn search hits into Startup records.

Try it with:  .venv\\Scripts\\python.exe -m searchbrain.extract "dexterous robotic hand" --industry robotics
"""

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from searchbrain import db, llm, programs
from searchbrain.log import get_logger
from searchbrain.models import Startup
from searchbrain.search import SearchHit, build_queries, search

logger = get_logger(__name__)

BATCH_SIZE = 2  # small batches keep each request short enough for the free NVIDIA tier
TEXT_PER_HIT = 1000
PARALLEL_REQUESTS = 1  # the free NVIDIA tier queues parallel requests per key, so more only causes timeouts
CACHE_FILE = Path("data") / "extract_cache.json"

# Regions are not countries and cannot be placed on the map as one marker
REGIONS = {
    "africa", "americas", "asia", "asia-pacific", "apac", "emea", "europe", "eu",
    "latin america", "middle east", "north america", "oceania", "south america", "worldwide", "global",
}

SYSTEM_PROMPT = """Reasoning: low
You extract startups from web search results.

Rules:
- Only use facts that are written in the given text. Never guess or use outside knowledge.
- If a field is not in the text, use null.
- Only young, independent companies (startups). Skip big corporations, universities,
  research projects, investors, media sites and market research firms.
- One search result can mention several startups (e.g. list articles). Extract each one,
  but at most 10 per search result: take the ones with the most facts.
- Keep "description" short: one sentence, at most 25 words.
- "link" is the startup's own website, only if it appears in the text. Otherwise null.
- "source_url" is the url of the search result where you found the startup.
- "founding_year" is a number like 2023, or null.
- "country" is one country name in English (e.g. "United States"). For regions like
  "Europe", "Asia-Pacific" or "North America" use null.
- "city" is only the city name (e.g. "New York", not "New York, New York").

Answer with JSON only, no other text, in exactly this form:
{"startups": [{"name": "...", "description": "one sentence", "industry": "...",
"country": "...", "city": "...", "founding_year": null, "link": null, "source_url": "..."}]}
If there are no startups, answer {"startups": []}."""


def _hits_as_text(hits: list[SearchHit]) -> str:
    return json.dumps(
        [{"title": hit.title, "url": hit.url, "text": hit.content[:TEXT_PER_HIT]} for hit in hits],
        ensure_ascii=False,
        indent=1,
    )


def parse_answer(answer: str) -> list[dict]:
    """Read the JSON object from the model answer, even if there is text around it."""
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    if not match:
        raise ValueError("No JSON object in the model answer")
    data = json.loads(match.group(0))
    startups = data.get("startups")
    if not isinstance(startups, list):
        raise ValueError('JSON has no "startups" list')
    return startups


def _clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"null", "none", "unknown", "n/a"} else None


def _clean_year(value) -> int | None:
    try:
        year = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return year if 1990 <= year <= date.today().year else None


def to_startup(raw: dict, allowed_sources: set[str], topic: str | None) -> Startup | None:
    """Turn one raw model record into a clean Startup, or None if it is unusable."""
    name = _clean_text(raw.get("name"))
    if not name:
        return None
    source_url = _clean_text(raw.get("source_url"))
    country = _clean_text(raw.get("country"))
    if country and country.lower() in REGIONS:
        country = None
    city = _clean_text(raw.get("city"))
    if city:
        city = city.split(",")[0].strip()  # "New York, New York" -> "New York"
    return Startup(
        name=name,
        description=_clean_text(raw.get("description")) or "",
        industry=(_clean_text(raw.get("industry")) or "").lower() or None,
        country=country,
        city=city,
        founding_year=_clean_year(raw.get("founding_year")),
        link=_clean_text(raw.get("link")),
        # The source must be one of our search results, never a made-up url
        source_url=source_url if source_url in allowed_sources else None,
        topic=topic,
    )


def merge_duplicates(startups: list[Startup]) -> list[Startup]:
    """Combine records of the same company (same domain or name), keeping the first known value."""
    merged: list[Startup] = []
    by_key: dict[str, Startup] = {}  # website key and name key both point to the same record
    for startup in startups:
        keys = {db.startup_key(startup), db.name_key(startup)}
        existing = next((by_key[key] for key in keys if key in by_key), None)
        if existing is None:
            existing = startup
            merged.append(startup)
        else:
            for field, value in vars(startup).items():
                if getattr(existing, field) in (None, "") and value not in (None, ""):
                    setattr(existing, field, value)
            existing.backed_by = programs.join_names(existing.backed_by, startup.backed_by)
        for key in keys | {db.startup_key(existing), db.name_key(existing)}:
            by_key[key] = existing
    return merged


def _batch_key(hits: list[SearchHit]) -> str:
    """Same hits + same prompt = same key, so a changed prompt is extracted again."""
    text = SYSTEM_PROMPT + _hits_as_text(hits)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def _extract_batch(hits: list[SearchHit]) -> list[dict] | None:
    """Ask the model about one batch of hits. Retry once if the answer is not valid JSON.

    Returns None if the batch failed, so it is not cached and a later run tries again.
    """
    for attempt in (1, 2):
        try:
            answer = llm.chat(SYSTEM_PROMPT, _hits_as_text(hits))
        except Exception as error:  # API errors, but also network errors in the middle of a stream
            logger.error("Extraction request failed (attempt %s): %s: %s", attempt, type(error).__name__, error)
            continue
        try:
            return parse_answer(answer)
        except (ValueError, json.JSONDecodeError) as error:
            logger.error("Extraction attempt %s failed: %s. Answer started with: %r", attempt, error, answer[:200])
    return None


def extract_startups(
    hits: list[SearchHit],
    topic: str | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[Startup]:
    """Extract startups from all hits, in batches, and merge duplicates.

    Successful batches are cached, failed batches are skipped (and retried on the next run).
    on_progress(batch_number, batch_count, status) is called after each batch.
    """
    allowed_sources = {hit.url for hit in hits}
    batches = [hits[start : start + BATCH_SIZE] for start in range(0, len(hits), BATCH_SIZE)]
    keys = [_batch_key(batch) for batch in batches]
    cache = _load_cache()
    results: dict[int, list[dict]] = {}
    finished = 0

    def report(status: str) -> None:
        nonlocal finished
        finished += 1
        if on_progress:
            on_progress(finished, len(batches), status)

    for index, key in enumerate(keys):
        if key in cache:
            results[index] = cache[key]
            report("cached")

    # The model calls run in parallel; cache and progress are only touched here in the main thread
    pending = [index for index in range(len(batches)) if index not in results]
    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
        futures = {pool.submit(_extract_batch, batches[index]): index for index in pending}
        for future in as_completed(futures):
            index = futures[future]
            raw_list = future.result()
            if raw_list is None:
                results[index] = []
                report("failed")
            else:
                results[index] = cache[keys[index]] = raw_list
                _save_cache(cache)
                report("done")

    startups = []
    for index in range(len(batches)):  # original order, so the result does not depend on timing
        for raw in results[index]:
            if isinstance(raw, dict) and (startup := to_startup(raw, allowed_sources, topic)):
                startups.append(startup)
    return merge_duplicates(startups)


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the web and extract startups (not saved yet).")
    parser.add_argument("keyword", nargs="?", default="")
    parser.add_argument("--industry", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--since", type=int, default=None)
    args = parser.parse_args()

    hits = search(build_queries(args.keyword, args.industry, args.country, args.since))
    print(f"{len(hits)} search hits, extracting ...\n")
    startups = extract_startups(hits, topic=" ".join(filter(None, [args.keyword, args.industry])))

    for s in startups:
        place = ", ".join(filter(None, [s.city, s.country])) or "location unknown"
        year = s.founding_year or "year unknown"
        print(f"- {s.name} ({place}, {year}) | {s.industry or 'industry unknown'}")
        print(f"  {s.description}")
        print(f"  website: {s.link or '-'} | source: {s.source_url or '-'}\n")
    print(f"{len(startups)} startups extracted")


if __name__ == "__main__":
    main()
