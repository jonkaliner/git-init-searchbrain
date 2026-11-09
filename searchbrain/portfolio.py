"""Portfolio pages of the accelerators: the pages that list their startups.

  discover  find the portfolio page(s) of each program once   ->  sources/portfolios.csv
  refresh   read those pages and store their text (table portfolio_pages)
  search    find the pages that mention your words, with the text around them

Only the first TOP_PROGRAMS programs of sources/programs.csv are used (the file is ordered by importance;
Y Combinator has its own list). robots.txt is respected (web.py). Pages that are built by JavaScript have
no text for us: they are stored with an empty text and show up in the report.

Try it with:  .venv313\\Scripts\\python.exe -m searchbrain.portfolio discover | refresh | search "robotics"
"""

import argparse
import csv
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from searchbrain import db, programs, web
from searchbrain.log import get_logger

logger = get_logger(__name__)

PORTFOLIOS_FILE = Path("sources") / "portfolios.csv"
COLUMNS = ["program", "website", "urls"]  # urls: up to MAX_PAGES addresses separated by a space
TOP_PROGRAMS = 75
MAX_PAGES = 3
MAX_TEXT = 6000  # characters stored per page
MIN_TEXT = 300  # less than this is a page that needs JavaScript
WORKERS = 8
SKIP = {"ycombinator.com"}  # has its own list (yc.py)
WORDS = re.compile(r"portfolio|startups?\b|companies|alumni|cohort|batch|investments|our-companies|founders", re.I)
COMMON_PATHS = ["/portfolio", "/startups", "/companies", "/alumni", "/our-startups", "/portfolio-companies", "/our-portfolio", "/community"]


def candidate_links(home: bytes, base: str) -> list[str]:
    """Links of the homepage on the same site whose address or text sounds like a list of startups, best first."""
    site = urlparse(base).netloc.removeprefix("www.")
    scored: dict[str, int] = {}
    for tag in BeautifulSoup(home, "html.parser").find_all("a", href=True):
        url = urljoin(base, tag["href"]).split("#")[0]
        if urlparse(url).netloc.removeprefix("www.") != site:
            continue
        score = 2 * bool(WORDS.search(urlparse(url).path)) + bool(WORDS.search(tag.get_text(" ", strip=True)))
        if score:
            scored[url] = max(score, scored.get(url, 0))
    return sorted(scored, key=lambda u: (-scored[u], len(u)))


def find_pages(website: str) -> list[str]:
    """Up to MAX_PAGES portfolio pages of a website that answer with enough text."""
    for base in (f"https://{website}", f"https://www.{website}"):
        try:
            home = web.get(base + "/")
        except (web.NotAllowed, requests.RequestException):
            continue
        found: list[str] = []
        tried: set[str] = set()
        for url in candidate_links(home, base + "/")[:6] + [base + path for path in COMMON_PATHS]:
            if len(found) >= MAX_PAGES:
                break
            if url.rstrip("/") in tried:  # /portfolio and /portfolio/ are the same page
                continue
            tried.add(url.rstrip("/"))
            try:
                if len(web.html_to_text(web.get(url), MAX_TEXT)) >= MIN_TEXT:
                    found.append(url)
            except (web.NotAllowed, requests.RequestException):
                continue
        return found
    return []


def top_programs() -> list[programs.Program]:
    return [p for p in programs.load_programs()[:TOP_PROGRAMS] if programs.domain_of(p.website) not in SKIP]


def load_pages(path: Path | None = None) -> list[dict]:
    path = path or PORTFOLIOS_FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def discover(program_list: list[programs.Program] | None = None, path: Path | None = None, finder=find_pages) -> list[dict]:
    """Find the portfolio pages of every program that is not in the file yet and save the result."""
    path = path or PORTFOLIOS_FILE
    program_list = program_list if program_list is not None else top_programs()
    known = {row["website"]: row for row in load_pages(path)}
    todo = [p for p in program_list if programs.domain_of(p.website) not in known]

    def look(program: programs.Program) -> dict:
        website = programs.domain_of(program.website)
        try:
            urls = finder(website)
        except Exception as error:  # one broken website must not stop the others
            logger.error("Portfolio search failed for %s: %s: %s", website, type(error).__name__, error)
            urls = []
        return {"program": program.name, "website": website, "urls": " ".join(urls)}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for row in pool.map(look, todo):
            known[row["website"]] = row
    rows = [known[programs.domain_of(p.website)] for p in program_list if programs.domain_of(p.website) in known]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, lineterminator=chr(10))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def refresh(conn: sqlite3.Connection, rows: list[dict] | None = None, fetch=web.get) -> dict:
    """Read every known portfolio page again and store its text."""
    rows = rows if rows is not None else load_pages()
    jobs = [(row["program"], url) for row in rows for url in row["urls"].split()]
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def read(job: tuple[str, str]) -> tuple[str, str, str | None]:
        try:
            return job[0], job[1], web.html_to_text(fetch(job[1]), MAX_TEXT)
        except Exception as error:  # a page that is down must not stop the others
            logger.warning("Could not read the portfolio page %s: %s", job[1], error)
            return job[0], job[1], None

    stored = failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for program, url, text in pool.map(read, jobs):
            if text is None:
                failed += 1
                continue
            conn.execute(
                "INSERT INTO portfolio_pages (url, program, text, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET program = excluded.program, text = excluded.text, fetched_at = excluded.fetched_at",
                (url, program, text, fetched_at),
            )
            stored += 1
    conn.commit()
    logger.info("Portfolio pages refreshed: %s stored, %s failed", stored, failed)
    return {"programs_with_pages": sum(1 for r in rows if r["urls"]), "pages": stored, "failed": failed}


def search_pages(conn: sqlite3.Connection, keyword: str, limit: int = 6, around: int = 350) -> list[dict]:
    """Portfolio pages that contain at least half of the words, with the text around the first hit."""
    stems = db.keyword_stems(keyword)
    if not stems:
        return []
    need = -(-len(stems) // 2)
    found = []
    for row in conn.execute("SELECT program, url, text FROM portfolio_pages"):
        text = row["text"] or ""
        lower = text.lower()
        hits = [lower.find(stem) for stem in stems if stem in lower]
        if len(hits) >= need:
            start = max(0, min(hits) - around // 2)
            found.append({"program": row["program"], "url": row["url"], "text": text[start : start + around * 2], "score": len(hits)})
    return sorted(found, key=lambda f: -f["score"])[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio pages of the accelerators.")
    parser.add_argument("action", choices=["discover", "refresh", "search"])
    parser.add_argument("keyword", nargs="?", default="")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    if args.action == "discover":
        rows = discover()
        print(f"{sum(1 for r in rows if r['urls'])} of {len(rows)} programs have a readable portfolio page ({PORTFOLIOS_FILE})")
    elif args.action == "refresh":
        print(refresh(db.connect()))
    else:
        for hit in search_pages(db.connect(), args.keyword):
            print(f"{hit['program']}: {hit['url']}\n   {hit['text'][:300]}\n")


if __name__ == "__main__":
    main()
