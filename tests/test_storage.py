from searchbrain import db, storage


def test_favorites_add_and_remove(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    storage.add_favorite(conn, "mimicrobotics.com")
    storage.add_favorite(conn, "mimicrobotics.com")  # twice is fine
    assert storage.favorite_keys(conn) == {"mimicrobotics.com"}

    storage.remove_favorite(conn, "mimicrobotics.com")
    assert storage.favorite_keys(conn) == set()


def test_scouts_survive_reconnect(tmp_path):
    path = tmp_path / "test.db"
    storage.save_search(db.connect(path), "Hands in China", {"keyword": "hand", "countries": ["China"]})

    scouts = storage.saved_searches(db.connect(path))  # like restarting the app
    assert [(s["name"], s["filters"]) for s in scouts] == [("Hands in China", {"keyword": "hand", "countries": ["China"]})]
    assert scouts[0]["last_seen_at"]

    storage.delete_search(db.connect(path), "Hands in China")
    assert storage.saved_searches(db.connect(path)) == []


def test_mark_scout_seen_moves_last_seen_forward(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    storage.save_search(conn, "Europe", {})
    conn.execute("UPDATE saved_searches SET last_seen_at = '2000-01-01T00:00:00+00:00'")
    storage.mark_search_seen(conn, "Europe")
    assert storage.saved_searches(conn)[0]["last_seen_at"] > "2000-01-01"


def test_chats_are_stored_with_messages(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    chat_id = storage.create_chat(conn, "Which startups build robot hands in China?")
    storage.save_messages(conn, chat_id, [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello", "found": ["x"]}])

    chat = storage.get_chat(db.connect(tmp_path / "test.db"), chat_id)
    assert chat["title"] == "Which startups build robot hands in China?"
    assert chat["messages"][1]["found"] == ["x"]
    assert [c["id"] for c in storage.list_chats(conn)] == [chat_id]

    storage.delete_chat(conn, chat_id)
    assert storage.get_chat(conn, chat_id) is None
