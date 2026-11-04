import sys

import pytest

from searchbrain import db, scouts, update


@pytest.fixture
def fake_steps(tmp_path, monkeypatch):
    """Replace every real step by a recorder."""
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    calls = []
    monkeypatch.setattr(update.yc, "import_all", lambda conn: calls.append("yc") or {"new": 1})
    monkeypatch.setattr(update.feeds, "discover", lambda: calls.append("discover") or [{"feed_url": "x"}])
    monkeypatch.setattr(update.feeds, "refresh", lambda conn: calls.append("refresh") or {"new_posts": 2})
    monkeypatch.setattr(update, "run_scouts", lambda conn, requests=None: calls.append(("scouts", requests)) or {"a": "1 matches"})
    monkeypatch.setattr(update.export, "export_site", lambda conn: calls.append("export") or {"startups": 1})
    return calls


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["update", *args])
    update.main()


def test_weekly_update_runs_all_steps_in_order(fake_steps, monkeypatch):
    run(monkeypatch)
    assert fake_steps == ["yc", "discover", "refresh", ("scouts", None), "export"]


def test_a_request_runs_only_that_scout_and_exports(fake_steps, monkeypatch):
    run(monkeypatch, "--request", "Build a deal flow for lidar")
    assert fake_steps == [("scouts", ["Build a deal flow for lidar"]), "export"]


def test_only_runs_one_step(fake_steps, monkeypatch):
    run(monkeypatch, "--only", "export")
    assert fake_steps == ["export"]


def test_a_failing_step_does_not_stop_the_others_but_fails_the_run(fake_steps, monkeypatch, capsys):
    def broken(conn):
        raise RuntimeError("Y Combinator list is down")

    monkeypatch.setattr(update.yc, "import_all", broken)
    with pytest.raises(SystemExit) as stop:
        run(monkeypatch)

    assert stop.value.code == 1
    assert fake_steps == ["discover", "refresh", ("scouts", None), "export"]  # everything after the failure still ran
    assert "Y Combinator list is down" in capsys.readouterr().out


def test_remove_deletes_a_scout_and_exports(fake_steps, monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "test.db")
    scout_id = scouts.add_scout(conn, "Build a deal flow for lidar")
    run(monkeypatch, "--remove", scout_id)
    assert scouts.all_scouts(db.connect(tmp_path / "test.db")) == []
    assert fake_steps == ["export"]
