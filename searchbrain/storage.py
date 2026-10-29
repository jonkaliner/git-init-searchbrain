"""Favorites, Scouts (saved searches) and chats for the web page, stored in the SQLite database."""

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- favorites ----------


def favorite_keys(conn: sqlite3.Connection) -> set[str]:
    return {row["key"] for row in conn.execute("SELECT key FROM favorites")}


def add_favorite(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("INSERT OR IGNORE INTO favorites (key, added_at) VALUES (?, ?)", (key, _now()))
    conn.commit()


def remove_favorite(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM favorites WHERE key = ?", (key,))
    conn.commit()


# ---------- Scouts (saved searches) ----------


def save_search(conn: sqlite3.Connection, name: str, filters: dict) -> None:
    """Save the filter values under a name (an existing Scout with that name is replaced)."""
    now = _now()
    conn.execute(
        "INSERT OR REPLACE INTO saved_searches (name, filters, saved_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (name.strip(), json.dumps(filters, ensure_ascii=False), now, now),
    )
    conn.commit()


def saved_searches(conn: sqlite3.Connection) -> list[dict]:
    """All Scouts, newest first: {"name", "filters", "last_seen_at"}."""
    rows = conn.execute("SELECT name, filters, saved_at, last_seen_at FROM saved_searches ORDER BY saved_at DESC")
    return [
        {"name": row["name"], "filters": json.loads(row["filters"]), "last_seen_at": row["last_seen_at"] or row["saved_at"]}
        for row in rows
    ]


def mark_search_seen(conn: sqlite3.Connection, name: str) -> None:
    """Remember that the Scout was opened now, so only later startups count as new."""
    conn.execute("UPDATE saved_searches SET last_seen_at = ? WHERE name = ?", (_now(), name))
    conn.commit()


def delete_search(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM saved_searches WHERE name = ?", (name,))
    conn.commit()


# ---------- chats ----------


def create_chat(conn: sqlite3.Connection, title: str) -> int:
    now = _now()
    cursor = conn.execute(
        "INSERT INTO chats (title, messages, created_at, updated_at) VALUES (?, '[]', ?, ?)", (title.strip()[:80], now, now)
    )
    conn.commit()
    return cursor.lastrowid


def save_messages(conn: sqlite3.Connection, chat_id: int, messages: list[dict]) -> None:
    conn.execute(
        "UPDATE chats SET messages = ?, updated_at = ? WHERE id = ?", (json.dumps(messages, ensure_ascii=False), _now(), chat_id)
    )
    conn.commit()


def get_chat(conn: sqlite3.Connection, chat_id: int) -> dict | None:
    row = conn.execute("SELECT id, title, messages, created_at FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "title": row["title"], "messages": json.loads(row["messages"]), "created_at": row["created_at"]}


def list_chats(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """Recent chats first: {"id", "title", "updated_at"}."""
    rows = conn.execute("SELECT id, title, updated_at FROM chats ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))
    return [dict(row) for row in rows]


def delete_chat(conn: sqlite3.Connection, chat_id: int) -> None:
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()
