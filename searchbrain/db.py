"""Local SQLite database for the collected startups."""

import re
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from searchbrain.models import Startup

DB_FILE = Path("data") / "searchbrain.db"

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
"""


LOCATION_FIELDS = ["city", "country", "latitude", "longitude", "location_precision"]
PRECISION_RANK = {"city": 3, "country": 2, "none": 1, None: 0}


def connect(path: Path = DB_FILE) -> sqlite3.Connection:
    """Open the database (and create it on first use)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _add_name_key_column(conn)
    return conn


def _add_name_key_column(conn: sqlite3.Connection) -> None:
    """Databases from before 2026-10-29 have no name_key column: add and fill it."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(startups)")}
    if "name_key" not in columns:
        conn.execute("ALTER TABLE startups ADD COLUMN name_key TEXT")
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


def _existing_key(conn: sqlite3.Connection, startup: Startup) -> str | None:
    """Find the stored row of this company: first by website/key, then by normalized name."""
    key = startup_key(startup)
    row = conn.execute(
        "SELECT key FROM startups WHERE key = ? OR name_key = ? ORDER BY key = ? DESC LIMIT 1",
        (key, name_key(startup), key),
    ).fetchone()
    return row["key"] if row else None


def upsert_startup(conn: sqlite3.Connection, startup: Startup) -> str:
    """Insert a new startup or fill in missing fields of an existing one. Returns "new" or "updated"."""
    existing = _existing_key(conn, startup)
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
    conn.commit()
    return "updated" if existing else "new"


def find_startup(conn: sqlite3.Connection, startup: Startup) -> Startup | None:
    """Return the stored version of this company (matched by website or name), or None."""
    key = _existing_key(conn, startup)
    if key is None:
        return None
    row = conn.execute(f"SELECT {', '.join(STARTUP_COLUMNS)} FROM startups WHERE key = ?", (key,)).fetchone()
    return Startup(**dict(row))


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


def all_startups(conn: sqlite3.Connection) -> list[Startup]:
    """Return every stored startup, sorted by name."""
    rows = conn.execute(f"SELECT {', '.join(STARTUP_COLUMNS)} FROM startups ORDER BY name COLLATE NOCASE")
    return [Startup(**dict(row)) for row in rows]
