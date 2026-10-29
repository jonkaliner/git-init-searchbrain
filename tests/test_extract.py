import pytest

from searchbrain import db
from searchbrain.extract import merge_duplicates, parse_answer, to_startup
from searchbrain.models import Startup

SOURCES = {"https://example.com/article"}


def test_parse_answer_ignores_text_around_json():
    answer = 'Here you go:\n{"startups": [{"name": "Mimic Robotics"}]}\nDone.'
    assert parse_answer(answer) == [{"name": "Mimic Robotics"}]


def test_parse_answer_rejects_missing_list():
    with pytest.raises(ValueError):
        parse_answer('{"companies": []}')


def test_to_startup_cleans_fields():
    raw = {
        "name": " Tangent Robotics ",
        "country": "United States",
        "city": "New York, New York",
        "founding_year": "2025",
        "link": "unknown",
        "source_url": "https://example.com/article",
    }
    startup = to_startup(raw, SOURCES, topic="robot hands")
    assert startup.name == "Tangent Robotics"
    assert startup.city == "New York"
    assert startup.founding_year == 2025
    assert startup.link is None


def test_to_startup_drops_regions_bad_years_and_unknown_sources():
    raw = {"name": "Stella-Robot", "country": "Asia-Pacific", "founding_year": "0000", "source_url": "https://made-up.com"}
    startup = to_startup(raw, SOURCES, topic=None)
    assert startup.country is None
    assert startup.founding_year is None
    assert startup.source_url is None


def test_to_startup_without_name_is_skipped():
    assert to_startup({"name": None}, SOURCES, topic=None) is None


def test_merge_duplicates_with_legal_suffix():
    merged = merge_duplicates([Startup(name="Proception"), Startup(name="Proception Inc", country="United States")])
    assert len(merged) == 1
    assert merged[0].country == "United States"


def test_legal_suffix_does_not_change_key():
    assert db.startup_key(Startup(name="Proception Inc.")) == db.startup_key(Startup(name="Proception"))
