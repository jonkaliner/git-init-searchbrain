"""Step 7: collect startups for a topic and store them.

search -> extract -> fill in from database -> geocode -> summarize -> save -> world map

Run with:
  .venv\\Scripts\\python.exe -m searchbrain.collect "humanoid robotics" --sub "robot hand" --sub "actuator"
"""

import argparse
import json
import re
from collections import Counter

from searchbrain import db, llm, programs, yc
from searchbrain.extract import extract_startups, merge_duplicates
from searchbrain.geocode import geocode_all
from searchbrain.log import get_logger
from searchbrain.models import Startup
from searchbrain.search import SearchHit, build_queries, search
from searchbrain.worldmap import save_map

logger = get_logger(__name__)

SUMMARY_BATCH = 10
PROGRAM_QUERIES = 2  # per group of program websites: the topic and the first subtopic

SUMMARY_PROMPT = """Reasoning: low
Write a short profile for each startup: 1-2 sentences, at most 40 words.
Only use the given facts. Do not add anything that is not in the facts.
Answer with JSON only, in the same order as the input:
{"summaries": ["...", "..."]}"""


def topic_queries(topic: str, subtopics: list[str]) -> list[str]:
    """The general queries for the topic plus one query per subtopic."""
    return build_queries(keyword=topic) + [f"{sub} startup {topic}" for sub in subtopics]


def fill_from_db(conn, startup: Startup) -> None:
    """Take over known fields (e.g. city, summary) from an earlier run."""
    existing = db.find_startup(conn, startup)
    if existing is None:
        return
    for field, value in vars(existing).items():
        if getattr(startup, field) in (None, "") and value not in (None, ""):
            setattr(startup, field, value)
    startup.backed_by = programs.join_names(existing.backed_by, startup.backed_by)


def summarize(startups: list[Startup]) -> None:
    """Add a summary to every startup that has none yet. Falls back to the description."""
    todo = [s for s in startups if not s.summary]
    for start in range(0, len(todo), SUMMARY_BATCH):
        batch = todo[start : start + SUMMARY_BATCH]
        facts = [
            {k: v for k, v in vars(s).items() if k in {"name", "description", "industry", "country", "city", "founding_year"} and v}
            for s in batch
        ]
        try:
            answer = llm.chat(SUMMARY_PROMPT, json.dumps(facts, ensure_ascii=False))
            summaries = json.loads(re.search(r"\{.*\}", answer, re.DOTALL).group(0))["summaries"]
            if len(summaries) != len(batch):
                raise ValueError(f"expected {len(batch)} summaries, got {len(summaries)}")
        except Exception as error:
            logger.error("Summary batch failed, using descriptions instead: %s", error)
            summaries = [None] * len(batch)
        for startup, summary in zip(batch, summaries):
            startup.summary = (summary or "").strip() or startup.description or None


def program_queries(topic: str, subtopics: list[str]) -> list[str]:
    """The queries sent to every group of program websites: the topic and the first subtopic."""
    return [f"{topic} startup", *(f"{sub} startup" for sub in subtopics[: PROGRAM_QUERIES - 1])]


def search_programs(topic: str, subtopics: list[str], program_list: list[programs.Program]) -> list[SearchHit]:
    """Search only the websites of the accelerators and incubators, one request per group of websites."""
    hits: list[SearchHit] = []
    for group in programs.search_domain_groups(program_list):
        hits += search(program_queries(topic, subtopics), include_domains=group)
    return hits


def program_search_count(subtopics: list[str]) -> int:
    """How many search requests (Tavily credits) the program search needs."""
    return len(programs.search_domain_groups(programs.load_programs())) * min(PROGRAM_QUERIES, 1 + len(subtopics))


def collect(topic: str, subtopics: list[str] | None = None, on_progress=None, sources: tuple[str, ...] = ("web",)) -> dict:
    """Run the whole collect step for one topic. Returns counts for the report.

    sources (any combination):
      "yc"       Y Combinator company list, takes seconds
      "programs" websites of the accelerators and incubators in sources/programs.csv
      "web"      the whole web
    "programs" and "web" need the AI extraction, which takes minutes.
    on_progress(batch_number, batch_count, status) is passed on to the extraction.
    """
    logger.info("Collect started: %s %s from %s", topic, subtopics or "", sources)
    conn = db.connect()
    subtopics = subtopics or []
    program_list = programs.load_programs()

    yc_startups = yc.find_startups(topic, subtopics) if "yc" in sources else []
    web_hits = search(topic_queries(topic, subtopics)) if "web" in sources else []
    program_hits = search_programs(topic, subtopics, program_list) if "programs" in sources else []
    hits = list({hit.url: hit for hit in web_hits + program_hits}.values())
    statuses = Counter()

    def track(number: int, count: int, status: str) -> None:
        statuses[status] += 1
        if on_progress:
            on_progress(number, count, status)

    web_startups = extract_startups(hits, topic=topic, on_progress=track) if hits else []
    for startup in web_startups:  # found on a program's website -> backed by that program
        startup.backed_by = programs.join_names(startup.backed_by, programs.program_for_url(startup.source_url, program_list))
    # YC first: its data is checked by YC, so it wins when the same company is found on the web too
    startups = merge_duplicates(yc_startups + web_startups)
    for startup in startups:
        fill_from_db(conn, startup)
    requests = geocode_all(conn, startups)
    summarize(startups)

    results = Counter(db.upsert_startup(conn, startup) for startup in startups)
    everything = db.all_startups(conn)
    map_file = save_map(everything)

    report = {
        "hits": len(hits),
        "program_hits": len(program_hits),
        "batches": statuses,
        "yc": len(yc_startups),
        "from_programs": sum(1 for s in startups if s.backed_by and s.backed_by != "Y Combinator"),
        "extracted": len(startups),
        "new": results["new"],
        "updated": results["updated"],
        "geocode_requests": requests,
        "precision": Counter(s.location_precision or "not geocoded" for s in startups),
        "total_in_db": len(everything),
        "map_file": map_file,
    }
    logger.info("Collect finished: %s", {k: v for k, v in report.items() if k != "map_file"})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect startups for a topic and save them.")
    parser.add_argument("topic")
    parser.add_argument("--sub", action="append", default=[], help="extra subtopic, can be repeated")
    parser.add_argument("--sources", default="web", help='comma separated: "web", "yc", "programs" (default: web)')
    args = parser.parse_args()
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())

    def show(number: int, count: int, status: str) -> None:
        print(f"  extraction batch {number}/{count}: {status}")

    try:
        report = collect(args.topic, args.sub, on_progress=show, sources=sources)
    except Exception:
        logger.exception("Collect failed")
        print("Collect failed, see logs/searchbrain.log")
        raise SystemExit(1)

    batches = report["batches"]
    print(f"Search hits:        {report['hits']}")
    print(f"Extraction batches: {dict(batches)}")
    if batches["failed"]:
        print(f"                    {batches['failed']} failed, run the same command again to retry only those")
    print(f"YC companies:       {report['yc']}")
    print(f"Startups extracted: {report['extracted']} ({report['new']} new, {report['updated']} updated)")
    print(f"Location:           {dict(report['precision'])} ({report['geocode_requests']} Nominatim requests)")
    print(f"Startups in DB:     {report['total_in_db']}")
    print(f"World map:          {report['map_file'].resolve()}")


if __name__ == "__main__":
    main()
