"""Scouts: deal flow requests that are saved and searched again every week, and their matches.

A Scout is just the text you would type into the Ask field, e.g.
"Build a deal flow for autonomous driving AI chip startups, early stage, Europe or US".
Its matches are startups from the database with a fit score (1-5 stars) and a reason.
"""

import re
import sqlite3
from datetime import datetime, timezone

FILLER = r"^(please\s+)?(build|make|create|find|give me|show me)\s+(me\s+)?(a\s+|the\s+)?(deal\s*flow\s*)?(for|of|on|with)?\s*"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(request: str) -> str:
    """A short id for a request: "Build a deal flow for AI chips, Europe" -> "ai-chips-europe"."""
    text = re.sub(FILLER, "", request.strip().lower())
    words = re.findall(r"[a-z0-9]+", text)[:6]
    return "-".join(words)[:50].strip("-") or "scout"


def add_scout(conn: sqlite3.Connection, request: str) -> str:
    """Save a request as a Scout and return its id. The same request twice is saved once."""
    request = " ".join(request.split())
    scout_id = slugify(request)
    conn.execute("INSERT OR IGNORE INTO scouts (id, request, created_at) VALUES (?, ?, ?)", (scout_id, request, _now()))
    conn.commit()
    return scout_id


def remove_scout(conn: sqlite3.Connection, scout_id: str) -> bool:
    """Delete a Scout and its matches. The startups stay in the database."""
    conn.execute("DELETE FROM matches WHERE scout_id = ?", (scout_id,))
    removed = conn.execute("DELETE FROM scouts WHERE id = ?", (scout_id,)).rowcount
    conn.commit()
    return bool(removed)


def all_scouts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in conn.execute("SELECT id, request, created_at, last_run_at FROM scouts ORDER BY created_at, id")]


def mark_run(conn: sqlite3.Connection, scout_id: str) -> None:
    conn.execute("UPDATE scouts SET last_run_at = ? WHERE id = ?", (_now(), scout_id))
    conn.commit()


def save_matches(conn: sqlite3.Connection, scout_id: str, matches: list[dict]) -> int:
    """Store the rated startups of a Scout run. Returns how many of them are new for this Scout.

    Each match: {"startup_key", "score", "why", "concerns"}. first_seen_at stays as it was,
    so "new this week" can be worked out later.
    """
    now = _now()
    new = 0
    for match in matches:
        known = conn.execute("SELECT 1 FROM matches WHERE scout_id = ? AND startup_key = ?", (scout_id, match["startup_key"])).fetchone()
        new += known is None
        conn.execute(
            """INSERT INTO matches (scout_id, startup_key, score, why, concerns, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(scout_id, startup_key) DO UPDATE SET
                 score = excluded.score, why = excluded.why, concerns = excluded.concerns, last_seen_at = excluded.last_seen_at""",
            (scout_id, match["startup_key"], match["score"], match.get("why"), match.get("concerns"), now, now),
        )
    conn.commit()
    return new


def matches_of(conn: sqlite3.Connection, scout_id: str) -> list[dict]:
    """The matches of a Scout, best fit first."""
    rows = conn.execute(
        "SELECT startup_key, score, why, concerns, first_seen_at, last_seen_at FROM matches WHERE scout_id = ? ORDER BY score DESC, first_seen_at DESC",
        (scout_id,),
    )
    return [dict(row) for row in rows]
