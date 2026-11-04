import json

from searchbrain import db, export, scouts
from searchbrain.models import Startup


def test_export_writes_compact_startups_scouts_and_meta(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.upsert_startup(
        conn,
        Startup(
            name="Mimic Robotics", description="AI robotic hand", summary="AI robotic hand", country="Switzerland", city="Zurich",
            latitude=47.376887, longitude=8.541694, location_precision="city", link="https://mimic.ai", yc_batch="W24", backed_by="Y Combinator",
        ),
    )
    db.upsert_startup(conn, Startup(name="Tangent", description="Robot hands", summary="Long text about robot hands.", topic="hands"))
    scout_id = scouts.add_scout(conn, "Build a deal flow for robot hands")
    scouts.save_matches(conn, scout_id, [{"startup_key": "mimic.ai", "score": 5, "why": "Seed stage.", "concerns": None}])

    meta = export.export_site(conn, tmp_path / "out")

    startups = json.loads((tmp_path / "out" / "startups.json").read_text(encoding="utf-8"))
    mimic = next(s for s in startups if s["name"] == "Mimic Robotics")
    assert mimic["key"] == "mimic.ai" and (mimic["lat"], mimic["lon"]) == (47.3769, 8.5417)
    assert "summary" not in mimic  # same as the description: not repeated
    assert mimic["batch"] == "W24" and mimic["programs"] == "Y Combinator" and len(mimic["added"]) == 10
    tangent = next(s for s in startups if s["name"] == "Tangent")
    assert tangent["summary"] == "Long text about robot hands." and "lat" not in tangent and "city" not in tangent  # empty fields are left out

    exported_scouts = json.loads((tmp_path / "out" / "scouts.json").read_text(encoding="utf-8"))
    assert exported_scouts[0]["id"] == scout_id and exported_scouts[0]["lastRun"] is None
    assert exported_scouts[0]["matches"][0] == {
        "key": "mimic.ai", "score": 5, "why": "Seed stage.", "concerns": None,
        "firstSeen": exported_scouts[0]["matches"][0]["firstSeen"], "lastSeen": exported_scouts[0]["matches"][0]["lastSeen"],
    }

    assert meta == json.loads((tmp_path / "out" / "meta.json").read_text(encoding="utf-8"))
    assert (meta["startups"], meta["located"], meta["yc"], meta["scouts"], meta["posts"]) == (2, 1, 1, 1, 0)
