"""Y Combinator companies as a second source, besides the web search.

Data: the public YC company directory, as an unofficial copy maintained by the
open-source project yc-oss (https://github.com/yc-oss/api). It is already structured,
so no AI extraction is needed: matching companies are found in seconds and cost no credits.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests

from searchbrain import db
from searchbrain.log import get_logger
from searchbrain.models import Startup

logger = get_logger(__name__)

YC_URL = "https://yc-oss.github.io/api/companies/all.json"
CACHE_FILE = Path("data") / "yc_companies.json"
MAX_AGE = timedelta(days=7)
STATUSES = ("Active",)  # only companies that are still running
MAX_RESULTS = 60

SEASONS = {"winter": "W", "spring": "X", "summer": "S", "fall": "F"}
COUNTRIES = {"usa": "United States", "us": "United States", "uk": "United Kingdom"}


def load_companies() -> list[dict]:
    """Return all YC companies. Downloads the list if the cache is missing or older than a week."""
    fresh = CACHE_FILE.exists() and datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime) < MAX_AGE
    if not fresh:
        try:
            response = requests.get(YC_URL, timeout=60)
            response.raise_for_status()
            companies = response.json()
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(companies, ensure_ascii=False), encoding="utf-8")
            return companies
        except (requests.RequestException, ValueError) as error:
            if not CACHE_FILE.exists():
                raise
            logger.error("Could not refresh the YC list, using the old copy: %s", error)
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


def short_batch(batch: str | None) -> str | None:
    """ "Winter 2024" -> "W24" (the way YC writes batches). Unknown formats -> None."""
    match = re.fullmatch(r"(winter|spring|summer|fall)\s+(\d{4})", (batch or "").strip().lower())
    return f"{SEASONS[match.group(1)]}{match.group(2)[2:]}" if match else None


def parse_location(all_locations: str | None) -> tuple[str | None, str | None]:
    """ "San Francisco, CA, USA; Burlingame, CA, USA" -> ("San Francisco", "United States")."""
    first = (all_locations or "").split(";")[0].strip()
    if not first or first.lower() == "remote":
        return None, None
    parts = [p.strip() for p in first.split(",") if p.strip()]
    country = COUNTRIES.get(parts[-1].lower(), parts[-1])
    city = parts[0] if len(parts) > 1 else None
    return city, country


def _text(company: dict) -> str:
    fields = [company.get("name"), company.get("one_liner"), company.get("long_description"), company.get("industry"),
              company.get("subindustry"), " ".join(company.get("tags") or []), " ".join(company.get("industries") or [])]
    return " ".join(str(f) for f in fields if f).lower()


def _summary(company: dict) -> str | None:
    """The first one or two sentences of the long description, at most about 300 characters."""
    text = (company.get("long_description") or "").strip()
    if not text:
        return company.get("one_liner")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:2])
    return summary if len(summary) <= 300 else summary[:297].rsplit(" ", 1)[0] + "…"


def to_startup(company: dict, topic: str | None) -> Startup:
    city, country = parse_location(company.get("all_locations"))
    subindustry = (company.get("subindustry") or "").split("->")[-1].strip()
    return Startup(
        name=company["name"].strip(),
        description=(company.get("one_liner") or "").strip(),
        summary=_summary(company),
        industry=(subindustry or company.get("industry") or "").lower() or None,
        city=city,
        country=country,
        link=company.get("website") or None,
        source_url=company.get("url") or None,
        topic=topic,
        yc_batch=short_batch(company.get("batch")),
        backed_by="Y Combinator",
    )


def find_startups(topic: str, subtopics: list[str] | None = None, companies: list[dict] | None = None) -> list[Startup]:
    """YC companies that match the topic or one of the subtopics (all words of a query must appear).

    Companies that mention the words in their one-liner or tags come first.
    """
    companies = companies if companies is not None else load_companies()
    queries = [db.keyword_stems(q) for q in [topic, *(subtopics or [])] if q.strip()]
    scored = []
    for company in companies:
        if company.get("status") not in STATUSES or not company.get("name"):
            continue
        text = _text(company)
        if not any(all(word in text for word in words) for words in queries):
            continue
        headline = f"{company.get('one_liner') or ''} {' '.join(company.get('tags') or [])}".lower()
        score = sum(all(word in headline for word in words) for words in queries)
        scored.append((score, company))
    scored.sort(key=lambda item: -item[0])
    return [to_startup(company, topic) for _, company in scored[:MAX_RESULTS]]
