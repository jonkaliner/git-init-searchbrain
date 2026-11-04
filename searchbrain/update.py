"""The weekly update (GitHub Actions runs this every Monday), or one deal flow on demand.

Steps, each on its own so that one failure does not stop the others:
  1. YC directory      all active Y Combinator companies, new ones are geocoded
  2. Accelerator feeds find new feeds, read all feeds, store the new posts
  3. Scouts            every saved deal flow request is searched again by the Scout and Analyst agents
  4. Export            the JSON files for the website (docs/data/)

Run it:
  .venv313\\Scripts\\python.exe -m searchbrain.update                                   the weekly update
  .venv313\\Scripts\\python.exe -m searchbrain.update --request "Build a deal flow for ..."   save and run one new Scout
  .venv313\\Scripts\\python.exe -m searchbrain.update --remove autonomous-driving-ai-chip   delete a Scout
  .venv313\\Scripts\\python.exe -m searchbrain.update --only export                    just rewrite the website data
"""

import argparse
import sys
import time

from searchbrain import db, export, feeds, scouts, yc
from searchbrain.log import get_logger

logger = get_logger(__name__)

STEPS = ["yc", "feeds", "scouts", "export"]


def run_step(name: str, function, problems: list[str]):
    """Run one step. A failure is logged and remembered, and the next step still runs."""
    started = time.time()
    try:
        result = function()
    except Exception as error:
        logger.exception("Update step failed: %s", name)
        problems.append(f"{name}: {type(error).__name__}: {error}")
        print(f"[FAILED] {name}: {type(error).__name__}: {error}", flush=True)
        return None
    print(f"[ok]     {name} ({time.time() - started:.0f} s): {result}", flush=True)
    return result


def run_scouts(conn, requests: list[str] | None = None) -> dict:
    """Run the given requests (they are saved as Scouts), or every saved Scout when none are given."""
    from searchbrain import crew  # imports CrewAI, which takes a few seconds: only when agents are needed

    requests = requests or [scout["request"] for scout in scouts.all_scouts(conn)]
    summary = {}
    for request in requests:
        print(f"         Scout: {request}", flush=True)
        try:
            deal_flow = crew.run(request, on_step=lambda line: print(f"           . {line}", flush=True), conn=conn)
        except Exception as error:  # one broken Scout must not stop the others
            logger.exception("Scout failed: %s", request)
            summary[request[:40]] = f"FAILED ({type(error).__name__})"
            continue
        summary[deal_flow.scout_id] = f"{len(deal_flow.matches)} matches, {deal_flow.new_matches} new" + (f", problems: {deal_flow.problems}" if deal_flow.problems else "")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly update of Searchbrain, or one deal flow on demand.")
    parser.add_argument("--request", action="append", default=[], help="save this deal flow request as a Scout and run only it (can be repeated)")
    parser.add_argument("--remove", action="append", default=[], help="delete the Scout with this id (can be repeated)")
    parser.add_argument("--only", choices=STEPS, help="run just one step")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")

    conn = db.connect()
    problems: list[str] = []

    for scout_id in args.remove:
        print(f"Scout '{scout_id}' removed" if scouts.remove_scout(conn, scout_id) else f"No Scout '{scout_id}'")

    on_demand = bool(args.request)
    chosen = [args.only] if args.only else (["scouts", "export"] if on_demand else STEPS if not args.remove else ["export"])

    if "yc" in chosen:
        run_step("YC directory", lambda: yc.import_all(conn), problems)
    if "feeds" in chosen:
        run_step("Accelerator feeds", lambda: {**{"programs_with_feed": sum(1 for r in feeds.discover() if r["feed_url"])}, **feeds.refresh(conn)}, problems)
    if "scouts" in chosen:
        run_step("Scouts", lambda: run_scouts(conn, args.request or None), problems)
    if "export" in chosen:
        run_step("Export for the website", lambda: export.export_site(conn), problems)

    if problems:
        print("\nProblems:\n  " + "\n  ".join(problems))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
