"""Local SQLite database for the collected startups."""

import re
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from searchbrain.models import Startup

DB_FILE = Path("data") / "searchbrain.db"

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
"""


def connect(path: Path = DB_FILE) -> sqlite3.Connection:
    """Open the database (and create it on first use)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def startup_key(startup: Startup) -> str:
    """One key per company: the website domain, or the normalized name if there is no link."""
    if startup.link:
        domain = urlparse(startup.link if "//" in startup.link else f"//{startup.link}").netloc.lower()
        domain = domain.removeprefix("www.")
        if domain:
            return domain
    return re.sub(r"[^a-z0-9]", "", startup.name.lower())


def upsert_startup(conn: sqlite3.Connection, startup: Startup) -> str:
    """Insert a new startup or fill in missing fields of an existing one. Returns "new" or "updated"."""
    key = startup_key(startup)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    exists = conn.execute("SELECT 1 FROM startups WHERE key = ?", (key,)).fetchone()

    columns = ", ".join(STARTUP_COLUMNS)
    placeholders = ", ".join("?" for _ in STARTUP_COLUMNS)
    # Keep known values: a later search without e.g. a city must not erase the city
    updates = ", ".join(f"{col} = COALESCE(NULLIF(excluded.{col}, ''), {col})" for col in STARTUP_COLUMNS)
    conn.execute(
        f"""INSERT INTO startups (key, {columns}, created_at, updated_at)
            VALUES (?, {placeholders}, ?, ?)
            ON CONFLICT(key) DO UPDATE SET {updates}, updated_at = excluded.updated_at""",
        [key, *asdict(startup).values(), now, now],
    )
    conn.commit()
    return "updated" if exists else "new"


def all_startups(conn: sqlite3.Connection) -> list[Startup]:
    """Return every stored startup, sorted by name."""
    rows = conn.execute(f"SELECT {', '.join(STARTUP_COLUMNS)} FROM startups ORDER BY name COLLATE NOCASE")
    return [Startup(**dict(row)) for row in rows]
