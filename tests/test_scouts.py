from searchbrain import db, scouts


def test_slugify_drops_the_filler_words():
    assert scouts.slugify("Build a deal flow for autonomous driving AI chip startups, early stage, Europe or US") == "autonomous-driving-ai-chip-startups-early"
    assert scouts.slugify("humanoid robotic hands") == "humanoid-robotic-hands"
    assert scouts.slugify("???") == "scout"


def test_same_request_is_one_scout_and_can_be_removed(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    first = scouts.add_scout(conn, "Build a deal flow for lidar   startups")
    again = scouts.add_scout(conn, "Build a deal flow for lidar startups")
    assert first == again == "lidar-startups"
    assert [s["request"] for s in scouts.all_scouts(conn)] == ["Build a deal flow for lidar startups"]

    scouts.save_matches(conn, first, [{"startup_key": "a.com", "score": 4, "why": "x"}])
    assert scouts.remove_scout(conn, first) is True
    assert scouts.all_scouts(conn) == [] and scouts.matches_of(conn, first) == []
    assert scouts.remove_scout(conn, first) is False


def test_matches_are_sorted_and_new_ones_counted(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    scout = scouts.add_scout(conn, "lidar")

    new = scouts.save_matches(conn, scout, [{"startup_key": "a.com", "score": 3, "why": "ok"}, {"startup_key": "b.com", "score": 5, "why": "great", "concerns": "late stage"}])
    assert new == 2
    assert [(m["startup_key"], m["score"]) for m in scouts.matches_of(conn, scout)] == [("b.com", 5), ("a.com", 3)]

    first_seen = scouts.matches_of(conn, scout)[1]["first_seen_at"]
    new = scouts.save_matches(conn, scout, [{"startup_key": "a.com", "score": 4, "why": "better now"}, {"startup_key": "c.com", "score": 2, "why": "weak"}])
    assert new == 1  # only c.com is new for this Scout
    a = next(m for m in scouts.matches_of(conn, scout) if m["startup_key"] == "a.com")
    assert (a["score"], a["why"], a["first_seen_at"]) == (4, "better now", first_seen)


def test_mark_run_sets_the_time(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    scout = scouts.add_scout(conn, "lidar")
    assert scouts.all_scouts(conn)[0]["last_run_at"] is None
    scouts.mark_run(conn, scout)
    assert scouts.all_scouts(conn)[0]["last_run_at"]
