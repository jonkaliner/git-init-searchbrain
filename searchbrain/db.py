"""Local SQLite database for the collected startups."""

import os
import re
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from searchbrain.models import Startup
from searchbrain.programs import join_names

DB_FILE = Path(os.getenv("SEARCHBRAIN_DB") or Path("data") / "searchbrain.db")  # the env variable is for tests with a copy

# Legal suffixes that do not change which company is meant ("Proception Inc" = "Proception")
LEGAL_SUFFIXES = r"\b(inc|incorporated|ltd|limited|llc|gmbh|ag|sa|sas|bv|plc|corp|corporation|co)\b\.?"

STARTUP_COLUMNS = [field.name for field in fields(Startup)]

SCHEMA = """
CREATE TABLE IF NOT EXISTS startups (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    industry TEXT,
    country TEXT,
    city TEXT,
    founding_year INTEGER,
    link TEXT,
    source_url TEXT,
    summary TEXT,
    latitude REAL,
    longitude REAL,
    location_precision TEXT,
    topic TEXT,
    yc_batch TEXT,
    backed_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    key TEXT PRIMARY KEY,
    latitude REAL,
    longitude REAL,
    found INTEGER NOT NULL,
    looked_up_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_entries (
    id TEXT PRIMARY KEY,
    program TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    summary TEXT,
    published TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_pages (
    url TEXT PRIMARY KEY,
    program TEXT NOT NULL,
    text TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scouts (
    id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    scout_id TEXT NOT NULL,
    startup_key TEXT NOT NULL,
    score INTEGER NOT NULL,
    why TEXT,
    concerns TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (scout_id, startup_key)
);
"""


LOCATION_FIELDS = ["city", "country", "latitude", "longitude", "location_precision"]
PRECISION_RANK = {"city": 3, "country": 2, "none": 1, None: 0}


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the database (and create it on first use). Default: DB_FILE."""
    path = path or DB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_column(conn, "startups", "yc_batch", "TEXT")
    _add_column(conn, "startups", "backed_by", "TEXT")
    # YC companies stored before backed_by existed
    conn.execute("UPDATE startups SET backed_by = 'Y Combinator' WHERE yc_batch IS NOT NULL AND COALESCE(backed_by, '') = ''")
    conn.commit()
    _add_name_key_column(conn)
    return conn


def _add_column(conn: sqlite3.Connection, table: str, column: str, sql_type: str) -> None:
    """Add a column that older databases do not have yet."""
    if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
        conn.commit()


def _add_name_key_column(conn: sqlite3.Connection) -> None:
    """Databases from before 2026-10-29 have no name_key column: add and fill it."""
    _add_column(conn, "startups", "name_key", "TEXT")
    for row in conn.execute("SELECT id, name FROM startups WHERE name_key IS NULL").fetchall():
        conn.execute("UPDATE startups SET name_key = ? WHERE id = ?", (name_key(Startup(name=row["name"])), row["id"]))
    conn.execute("CREATE INDEX IF NOT EXISTS startups_name_key ON startups (name_key)")
    conn.commit()


def name_key(startup: Startup) -> str:
    """The normalized name: lowercase, without legal suffixes and punctuation."""
    name = re.sub(LEGAL_SUFFIXES, "", startup.name.lower())
    return re.sub(r"[^a-z0-9]", "", name)


def startup_key(startup: Startup) -> str:
    """One key per company: the website domain, or the normalized name if there is no link."""
    if startup.link:
        domain = urlparse(startup.link if "//" in startup.link else f"//{startup.link}").netloc.lower()
        domain = domain.removeprefix("www.")
        if domain:
            return domain
    return name_key(startup)


def existing_key(conn: sqlite3.Connection, startup: Startup) -> str | None:
    """Find the stored row of this company: first by website/key, then by normalized name."""
    key = startup_key(startup)
    row = conn.execute(
        "SELECT key FROM startups WHERE key = ? OR name_key = ? ORDER BY key = ? DESC LIMIT 1",
        (key, name_key(startup), key),
    ).fetchone()
    return row["key"] if row else None


def upsert_startup(conn: sqlite3.Connection, startup: Startup, commit: bool = True) -> str:
    """Insert a new startup or fill in missing fields of an existing one. Returns "new" or "updated".

    commit=False is for saving thousands of rows in one go: the caller commits at the end.
    """
    existing = existing_key(conn, startup)
    key = existing or startup_key(startup)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    columns = ", ".join(STARTUP_COLUMNS)
    placeholders = ", ".join("?" for _ in STARTUP_COLUMNS)
    # Keep known values: a later search without e.g. a city must not erase the city
    updates = ", ".join(f"{col} = COALESCE(NULLIF(excluded.{col}, ''), {col})" for col in STARTUP_COLUMNS)
    conn.execute(
        f"""INSERT INTO startups (key, name_key, {columns}, created_at, updated_at)
            VALUES (?, ?, {placeholders}, ?, ?)
            ON CONFLICT(key) DO UPDATE SET {updates}, updated_at = excluded.updated_at""",
        [key, name_key(startup), *asdict(startup).values(), now, now],
    )
    if commit:
        conn.commit()
    return "updated" if existing else "new"


def find_startup(conn: sqlite3.Connection, startup: Startup) -> Startup | None:
    """Return the stored version of this company (matched by website or name), or None."""
    key = existing_key(conn, startup)
    if key is None:
        return None
    row = conn.execute(f"SELECT {', '.join(STARTUP_COLUMNS)} FROM startups WHERE key = ?", (key,)).fetchone()
    return Startup(**dict(row))


def fill_from_db(conn: sqlite3.Connection, startup: Startup) -> None:
    """Take over known fields (e.g. city, summary) from the stored version of this company."""
    existing = find_startup(conn, startup)
    if existing is None:
        return
    for field, value in vars(existing).items():
        if getattr(startup, field) in (None, "") and value not in (None, ""):
            setattr(startup, field, value)
    startup.backed_by = join_names(existing.backed_by, startup.backed_by)


def merge_duplicates(conn: sqlite3.Connection) -> list[str]:
    """Merge rows that are the same company (same normalized name) into the oldest row.

    Missing fields are filled from the other rows; the location is taken as a whole from the
    row with the best precision (city > country > none). The other rows are deleted.
    Returns the names of the merged companies.
    """
    groups = conn.execute("SELECT name_key FROM startups GROUP BY name_key HAVING COUNT(*) > 1").fetchall()
    merged = []
    for group in groups:
        rows = conn.execute(
            f"SELECT id, {', '.join(STARTUP_COLUMNS)} FROM startups WHERE name_key = ? ORDER BY id", (group["name_key"],)
        ).fetchall()
        records = [Startup(**{col: row[col] for col in STARTUP_COLUMNS}) for row in rows]
        keep = records[0]
        for other in records[1:]:
            for field in STARTUP_COLUMNS:
                if field not in LOCATION_FIELDS and getattr(keep, field) in (None, "") and getattr(other, field) not in (None, ""):
                    setattr(keep, field, getattr(other, field))
        best = max(records, key=lambda s: PRECISION_RANK.get(s.location_precision, 0))
        for field in LOCATION_FIELDS:
            setattr(keep, field, getattr(best, field))

        assignments = ", ".join(f"{col} = ?" for col in STARTUP_COLUMNS)
        conn.execute(f"UPDATE startups SET {assignments} WHERE id = ?", [*asdict(keep).values(), rows[0]["id"]])
        conn.executemany("DELETE FROM startups WHERE id = ?", [(row["id"],) for row in rows[1:]])
        merged.append(keep.name)
    conn.commit()
    return merged


COUNTRY_ALIASES = {
    "usa": "united states", "us": "united states", "u.s.": "united states", "america": "united states",
    "uk": "united kingdom", "britain": "united kingdom", "great britain": "united kingdom",
    "korea": "south korea", "holland": "netherlands",
}


def keyword_stems(keyword: str | None) -> list[str]:
    """Search words without a plural "s", so "sensors" also finds "sensor"."""
    return [word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word for word in (keyword or "").lower().split()]


def search_startups(
    conn: sqlite3.Connection,
    keyword: str | None = None,
    countries: list[str] | None = None,
    city: str | None = None,
    founded_since: int | None = None,
    limit: int = 15,
) -> list[Startup]:
    """Search the stored startups. All given filters must match.

    keyword: every word must appear somewhere in name, description, summary or industry
    ("robot hand" also finds "robotic hands").
    """
    where, params = [], []
    for word in keyword_stems(keyword):
        where.append("LOWER(name || ' ' || COALESCE(description, '') || ' ' || COALESCE(summary, '') || ' ' || COALESCE(industry, '')) LIKE ?")
        params.append(f"%{word}%")
    names = [COUNTRY_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in countries or [] if c.strip()]
    if names:
        where.append(f"LOWER(country) IN ({', '.join('?' for _ in names)})")
        params.extend(names)
    if city:
        where.append("LOWER(city) = ?")
        params.append(city.strip().lower())
    if founded_since:
        where.append("founding_year >= ?")
        params.append(founded_since)

    sql = f"SELECT {', '.join(STARTUP_COLUMNS)} FROM startups"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name COLLATE NOCASE LIMIT ?"
    rows = conn.execute(sql, [*params, limit])
    return [Startup(**dict(row)) for row in rows]


GENERIC_WORDS = {"startup", "company", "and", "the", "for", "with", "from"}  # say nothing about the topic


def search_ranked(conn: sqlite3.Connection, keyword: str, countries: list[str] | None = None, limit: int = 12) -> list[Startup]:
    """Search like a person: a startup does not need every word, the more words it matches the higher it ranks.

    At least half of the words must match (anywhere). A word in the name or the one-line description
    counts double compared to a word in the industry or the long summary.
    """
    stems = [stem for stem in keyword_stems(keyword) if stem not in GENERIC_WORDS] or keyword_stems(keyword)
    if not stems:
        return []
    headline = "LOWER(name || ' ' || COALESCE(description, ''))"
    background = "LOWER(COALESCE(industry, '') || ' ' || COALESCE(summary, ''))"
    score = " + ".join(f"(CASE WHEN {headline} LIKE ? THEN 2 ELSE 0 END) + (CASE WHEN {background} LIKE ? THEN 1 ELSE 0 END)" for _ in stems)
    matched = " + ".join(f"(CASE WHEN {headline} LIKE ? OR {background} LIKE ? THEN 1 ELSE 0 END)" for _ in stems)
    params: list = []
    for stem in stems:
        params += [f"%{stem}%"] * 2
    for stem in stems:
        params += [f"%{stem}%"] * 2

    where = ""
    names = [COUNTRY_ALIASES.get(c.strip().lower(), c.strip().lower()) for c in countries or [] if c.strip()]
    if names:
        where = f"WHERE LOWER(country) IN ({', '.join('?' for _ in names)})"
        params += names

    sql = f"""SELECT {', '.join(STARTUP_COLUMNS)} FROM (
                  SELECT *, {score} AS score, {matched} AS matched FROM startups {where}
              ) WHERE matched >= ? ORDER BY score DESC, name COLLATE NOCASE LIMIT ?"""
    rows = conn.execute(sql, [*params, -(-len(stems) // 2), limit])
    return [Startup(**{col: row[col] for col in STARTUP_COLUMNS}) for row in rows]


def all_startups(conn: sqlite3.Connection) -> list[Startup]:
    """Return every stored startup, sorted by name."""
    return [startup for _, startup in all_startups_with_keys(conn)]


def topics(conn: sqlite3.Connection) -> list[str]:
    """The areas (collect topics) in the database, e.g. ["humanoid robotics", "lidar sensors"]."""
    return [row["topic"] for row in conn.execute("SELECT DISTINCT topic FROM startups WHERE topic IS NOT NULL ORDER BY topic")]


def created_times(conn: sqlite3.Connection) -> dict[str, str]:
    """When each startup (by key) was first stored, e.g. to show what is new for a Scout."""
    return {row["key"]: row["created_at"] for row in conn.execute("SELECT key, created_at FROM startups")}


def all_startups_with_keys(conn: sqlite3.Connection) -> list[tuple[str, Startup]]:
    """Like all_startups, but with each row's key."""
    rows = conn.execute(f"SELECT key, {', '.join(STARTUP_COLUMNS)} FROM startups ORDER BY name COLLATE NOCASE")
    return [(row["key"], Startup(**{col: row[col] for col in STARTUP_COLUMNS})) for row in rows]
