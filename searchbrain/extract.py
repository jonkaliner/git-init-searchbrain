"""Step 5: let the NVIDIA model turn search hits into Startup records.

Try it with:  .venv\\Scripts\\python.exe -m searchbrain.extract "dexterous robotic hand" --industry robotics
"""

import argparse
import json
import re
from datetime import date

from searchbrain import db, llm
from searchbrain.log import get_logger
from searchbrain.models import Startup
from searchbrain.search import SearchHit, build_queries, search

logger = get_logger(__name__)

BATCH_SIZE = 5

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
- One search result can mention several startups (e.g. list articles). Extract each one.
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
        [{"title": hit.title, "url": hit.url, "text": hit.content[:1500]} for hit in hits],
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
        industry=_clean_text(raw.get("industry")),
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
    merged: dict[str, Startup] = {}
    for startup in startups:
        key = db.startup_key(startup)
        if key not in merged:
            merged[key] = startup
            continue
        existing = merged[key]
        for field, value in vars(startup).items():
            if getattr(existing, field) in (None, "") and value not in (None, ""):
                setattr(existing, field, value)
    return list(merged.values())


def _extract_batch(hits: list[SearchHit]) -> list[dict]:
    """Ask the model about one batch of hits. Retry once if the answer is not valid JSON."""
    for attempt in (1, 2):
        answer = llm.chat(SYSTEM_PROMPT, _hits_as_text(hits))
        try:
            return parse_answer(answer)
        except (ValueError, json.JSONDecodeError) as error:
            logger.error("Extraction attempt %s failed: %s. Answer started with: %r", attempt, error, answer[:200])
    return []


def extract_startups(hits: list[SearchHit], topic: str | None = None) -> list[Startup]:
    """Extract startups from all hits, in batches, and merge duplicates."""
    allowed_sources = {hit.url for hit in hits}
    startups = []
    for start in range(0, len(hits), BATCH_SIZE):
        for raw in _extract_batch(hits[start : start + BATCH_SIZE]):
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
