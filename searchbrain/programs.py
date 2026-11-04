"""Accelerators, incubators and other startup programs (sources/programs.csv).

They are used as a search scope: the web search can be limited to their websites,
e.g. portfolio or demo day pages, and a startup found there is marked as "backed by" that program.
Nothing is crawled: only pages that the search engine returns are read.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PROGRAMS_FILE = Path("sources") / "programs.csv"
DOMAINS_PER_SEARCH = 20  # websites per search request, so one request does not get too broad
SKIP_FOR_SEARCH = {"ycombinator.com"}  # YC is covered better by its company directory (yc.py)


@dataclass(frozen=True)
class Program:
    name: str
    type: str
    city: str
    country: str
    region: str
    focus: str
    website: str


def load_programs(path: Path | None = None) -> list[Program]:
    with (path or PROGRAMS_FILE).open(encoding="utf-8", newline="") as file:
        return [
            Program(**{field: row[field].strip() for field in Program.__dataclass_fields__})
            for row in csv.DictReader(file)
            if row.get("name") and row.get("website")
        ]


def domain_of(url: str | None) -> str:
    """ "https://www.hax.co/portfolio" -> "hax.co" """
    if not url:
        return ""
    netloc = urlparse(url if "//" in url else f"//{url}").netloc.lower()
    return netloc.removeprefix("www.")


def program_for_url(url: str | None, programs: list[Program]) -> str | None:
    """The program whose website the page belongs to (also for subdomains), or None."""
    domain = domain_of(url)
    if not domain:
        return None
    for program in programs:
        site = domain_of(program.website)
        if domain == site or domain.endswith(f".{site}"):
            return program.name
    return None


def join_names(*values: str | None) -> str | None:
    """Combine program lists without duplicates: ("Y Combinator", "HAX; Y Combinator") -> "Y Combinator; HAX"."""
    names: list[str] = []
    for value in values:
        for name in (value or "").split(";"):
            if name.strip() and name.strip() not in names:
                names.append(name.strip())
    return "; ".join(names) or None


def search_domain_groups(programs: list[Program]) -> list[list[str]]:
    """The program websites in groups of DOMAINS_PER_SEARCH, one search request per group."""
    domains = sorted({domain_of(p.website) for p in programs} - SKIP_FOR_SEARCH)
    return [domains[i : i + DOMAINS_PER_SEARCH] for i in range(0, len(domains), DOMAINS_PER_SEARCH)]
