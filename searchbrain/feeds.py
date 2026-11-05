"""RSS and Atom feeds of the accelerators and incubators (sources/programs.csv).

Why feeds: they are made to be read by programs, they list the newest posts (new cohorts,
demo days, portfolio news) and they need no search service. Two steps:

  discover  find the feed address of every program website, once  ->  sources/feeds.csv
  refresh   read all feeds and store the new posts in the table feed_entries

Try it with:
  .venv313\\Scripts\\python.exe -m searchbrain.feeds discover
  .venv313\\Scripts\\python.exe -m searchbrain.feeds refresh
  .venv313\\Scripts\\python.exe -m searchbrain.feeds search "robot hand"
"""

import argparse
import csv
import hashlib
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

from searchbrain import db, programs, web
from searchbrain.log import get_logger

logger = get_logger(__name__)

FEEDS_FILE = Path("sources") / "feeds.csv"
FEED_COLUMNS = ["program", "website", "feed_url", "checked_at"]
COMMON_FEED_PATHS = [
    "/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml",
    "/blog/feed", "/blog/rss.xml", "/blog/feed.xml", "/news/feed", "/news/rss.xml",
]
FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json", "text/xml", "application/xml"}
RECHECK_AFTER = timedelta(days=90)  # a program without a feed is looked at again after this time
KEEP_DAYS = 180  # older posts are removed from the database
MAX_SUMMARY = 1200  # characters of a post that are stored
WORKERS = 8  # different websites in parallel; each website only gets a few requests


def parse_feed(body: bytes) -> list[dict]:
    """The posts of an RSS or Atom feed (title, link, summary, published). Empty if the body is not a feed."""
    entries = []
    for entry in feedparser.parse(body).entries:
        title, link = (entry.get("title") or "").strip(), (entry.get("link") or "").strip()
        if not title or not link:
            continue
        content = entry.get("summary") or (entry.get("content") or [{}])[0].get("value", "")
        moment = entry.get("published_parsed") or entry.get("updated_parsed")
        entries.append(
            {
                "title": web.html_to_text(title),
                "link": link,
                "summary": web.html_to_text(content, MAX_SUMMARY),
                "published": time.strftime("%Y-%m-%d", moment) if moment else None,
            }
        )
    return entries


def _is_feed(url: str) -> bool:
    try:
        return bool(parse_feed(web.get(url)))
    except (web.NotAllowed, requests.RequestException):
        return False


def linked_feeds(html: bytes, base: str) -> list[str]:
    """Feed addresses that a page announces with <link rel="alternate" type="application/rss+xml" href="...">."""
    found = []
    for tag in BeautifulSoup(html, "html.parser").find_all("link", href=True):
        rel = tag.get("rel") or []
        if "alternate" in [r.lower() for r in rel] and (tag.get("type") or "").lower() in FEED_TYPES:
            found.append(urljoin(base, tag["href"]))
    return found


def find_feed(website: str) -> str | None:
    """The feed address of a website ("hax.co"): first what the homepage announces, then common paths."""
    for base in (f"https://{website}", f"https://www.{website}"):
        try:
            home = web.get(base + "/")
        except (web.NotAllowed, requests.RequestException):
            continue  # try the other spelling
        candidates = linked_feeds(home, base + "/") + [base + path for path in COMMON_FEED_PATHS]
        for url in dict.fromkeys(candidates):  # without duplicates, in order
            if _is_feed(url):
                return url
        return None  # the site answers but has no feed
    return None


def load_feeds(path: Path | None = None) -> list[dict]:
    path = path or FEEDS_FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def save_feeds(rows: list[dict], path: Path | None = None) -> None:
    path = path or FEEDS_FILE
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FEED_COLUMNS, lineterminator="
")  # not the csv default 
: git would see every run as a change
        writer.writeheader()
        writer.writerows(rows)


def _needs_check(row: dict | None, now: datetime) -> bool:
    if row is None:
        return True
    if row["feed_url"]:
        return False
    try:
        return now - datetime.fromisoformat(row["checked_at"]) > RECHECK_AFTER
    except ValueError:
        return True


def discover(program_list: list[programs.Program] | None = None, path: Path | None = None, finder=find_feed) -> list[dict]:
    """Find the feed of every program that has not been checked yet and save the result.

    Programs without a feed are listed too (empty feed_url), so they are not tried again every time.
    """
    program_list = program_list if program_list is not None else programs.load_programs()
    known = {row["website"]: row for row in load_feeds(path)}
    now = datetime.now(timezone.utc)
    todo = [p for p in program_list if _needs_check(known.get(programs.domain_of(p.website)), now)]

    def look(program: programs.Program) -> dict:
        website = programs.domain_of(program.website)
        try:
            feed_url = finder(website) or ""
        except Exception as error:  # one broken website must not stop the other 99
            logger.error("Feed search failed for %s: %s: %s", website, type(error).__name__, error)
            feed_url = ""
        return {"program": program.name, "website": website, "feed_url": feed_url, "checked_at": now.isoformat(timespec="seconds")}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for row in pool.map(look, todo):
            known[row["website"]] = row
    rows = [known[programs.domain_of(p.website)] for p in program_list if programs.domain_of(p.website) in known]
    save_feeds(rows, path)
    return rows


def refresh(conn: sqlite3.Connection, rows: list[dict] | None = None, fetch=web.get) -> dict:
    """Read every known feed and store the posts that are new. Returns counts for the report."""
    rows = [row for row in (rows if rows is not None else load_feeds()) if row["feed_url"]]
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def read(row: dict) -> tuple[dict, list[dict] | None]:
        try:
            return row, parse_feed(fetch(row["feed_url"]))
        except Exception as error:  # a feed that is down must not stop the others
            logger.warning("Could not read the feed of %s (%s): %s", row["program"], row["feed_url"], error)
            return row, None

    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    new = failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for row, entries in pool.map(read, rows):
            if entries is None:
                failed += 1
                continue
            for entry in entries:
                if entry["published"] and entry["published"] < cutoff:
                    continue  # too old to be stored
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO feed_entries (id, program, title, link, summary, published, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (hashlib.sha1(entry["link"].encode()).hexdigest()[:16], row["program"], entry["title"], entry["link"],
                     entry["summary"], entry["published"], fetched_at),
                )
                new += cursor.rowcount
    removed = conn.execute("DELETE FROM feed_entries WHERE COALESCE(published, substr(fetched_at, 1, 10)) < ?", (cutoff,)).rowcount
    conn.commit()
    logger.info("Feeds refreshed: %s feeds, %s failed, %s new posts, %s old posts removed", len(rows), failed, new, removed)
    return {"feeds": len(rows), "failed": failed, "new_posts": new, "removed": removed}


def search_entries(conn: sqlite3.Connection, keyword: str, limit: int = 10) -> list[dict]:
    """Posts that contain all words of the keyword (plural-tolerant), newest first."""
    where, params = [], []
    for word in db.keyword_stems(keyword):
        where.append("LOWER(title || ' ' || COALESCE(summary, '')) LIKE ?")
        params.append(f"%{word}%")
    sql = "SELECT program, title, link, summary, published FROM feed_entries"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(published, substr(fetched_at, 1, 10)) DESC LIMIT ?"
    return [dict(row) for row in conn.execute(sql, [*params, limit])]


def main() -> None:
    parser = argparse.ArgumentParser(description="Find, read and search the RSS feeds of the accelerators.")
    parser.add_argument("action", choices=["discover", "refresh", "search"])
    parser.add_argument("keyword", nargs="?", default="")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # the Windows console cannot print every character of a post

    if args.action == "discover":
        rows = discover()
        with_feed = [row for row in rows if row["feed_url"]]
        print(f"{len(with_feed)} of {len(rows)} programs have a feed (saved in {FEEDS_FILE})")
    elif args.action == "refresh":
        print(refresh(db.connect()))
    else:
        for entry in search_entries(db.connect(), args.keyword):
            print(f"{entry['published'] or '          '}  {entry['program']}: {entry['title']}\n            {entry['link']}")


if __name__ == "__main__":
    main()
