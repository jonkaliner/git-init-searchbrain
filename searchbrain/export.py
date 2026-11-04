"""Export the database as JSON files for the website (docs/data/).

GitHub Pages can only show files, so the website reads these files in the browser:
  startups.json  every startup (name, what it does, place, links, ...), empty fields left out
  scouts.json    every Scout with its request and its matches (stars, why, concerns)
  meta.json      when the export was made and a few counts
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from searchbrain import db, scouts
from searchbrain.log import get_logger

logger = get_logger(__name__)

DATA_DIR = Path("docs") / "data"


def startup_record(key: str, row: sqlite3.Row) -> dict:
    """One startup as the website needs it: short, without empty fields."""
    record = {
        "key": key,
        "name": row["name"],
        "description": row["description"],
        "industry": row["industry"],
        "country": row["country"],
        "city": row["city"],
        "year": row["founding_year"],
        "link": row["link"],
        "source": row["source_url"],
        # the long summary only if it says more than the one-liner
        "summary": row["summary"] if row["summary"] and row["summary"] != row["description"] else None,
        "lat": round(row["latitude"], 4) if row["latitude"] is not None else None,
        "lon": round(row["longitude"], 4) if row["longitude"] is not None else None,
        "precision": row["location_precision"],
        "topic": row["topic"],
        "batch": row["yc_batch"],
        "programs": row["backed_by"],
        "added": (row["created_at"] or "")[:10],
    }
    return {name: value for name, value in record.items() if value not in (None, "")}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export_site(conn: sqlite3.Connection, out_dir: Path | None = None) -> dict:
    """Write the three JSON files and return the counts."""
    out_dir = out_dir or DATA_DIR
    rows = conn.execute("SELECT * FROM startups ORDER BY name COLLATE NOCASE").fetchall()
    startups = [startup_record(row["key"], row) for row in rows]

    scout_list = []
    for scout in scouts.all_scouts(conn):
        matches = scouts.matches_of(conn, scout["id"])
        scout_list.append(
            {
                "id": scout["id"],
                "request": scout["request"],
                "created": scout["created_at"][:10],
                "lastRun": (scout["last_run_at"] or "")[:10] or None,
                "matches": [
                    {
                        "key": m["startup_key"], "score": m["score"], "why": m["why"], "concerns": m["concerns"],
                        "firstSeen": m["first_seen_at"][:10], "lastSeen": m["last_seen_at"][:10],
                    }
                    for m in matches
                ],
            }
        )

    meta = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "startups": len(startups),
        "located": sum(1 for s in startups if "lat" in s),
        "yc": sum(1 for s in startups if "batch" in s),
        "scouts": len(scout_list),
        "posts": conn.execute("SELECT COUNT(*) FROM feed_entries").fetchone()[0],
    }
    write_json(out_dir / "startups.json", startups)
    write_json(out_dir / "scouts.json", scout_list)
    write_json(out_dir / "meta.json", meta)
    logger.info("Site data exported: %s", meta)
    return meta
