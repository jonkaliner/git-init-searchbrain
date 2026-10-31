"""Step 8: chat agent that answers questions from the stored startups.

The NVIDIA model gets three tools:
- search_startups queries the SQLite database,
- show_matches picks the fitting startups with a fit score and a reason (the page shows a profile for each),
- propose_web_search suggests a web search when the database has too little.
The final text answer is kept short, because long answers are slow on the free NVIDIA tier.

Run with:  .venv\\Scripts\\python.exe -m searchbrain.agent
      or:  .venv\\Scripts\\python.exe -m searchbrain.agent "Which startups build robot hands in China?"
"""

import json
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from searchbrain import config, db, llm
from searchbrain.log import get_logger
from searchbrain.models import Startup

logger = get_logger(__name__)

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """Reasoning: low
You answer questions about startups stored in the Searchbrain database.
Rules:
- Use the search_startups tool before answering any question about startups.
  Correct obvious typos in the search words (e.g. "humanoind" -> "humanoid").
- Answer only with facts from the tool results. Never use outside knowledge about companies.
- After searching, call show_matches once with every startup from the results that fits the question,
  best fit first, each with a fit score from 1 to 5 and one short sentence why it fits.
  The page then shows a profile for each (location, founding year, YC batch, summary, links).
- Your final text answer is then only 1-2 sentences, e.g. "I found 6 potential matches. The best fits
  are Proception and Nori." Do not list the startups or their details again.
- If nothing is found, you may search once more with fewer or broader words.
  If there is still nothing, say that the database has no matching startups.
- For regions like Europe or Asia, pass the list of countries in that region.
- If the question only asks about a place or a founding year (no technology), search without keyword.
  Words like "startup", "company" or the general topic of the database are not keywords.
- If a value (city, founding year) is unknown, say it is unknown. Do not guess.
- "yc_batch" (e.g. "W24") means the startup was in Y Combinator, Winter 2024. Mention it as "YC W24".
  It is not the founding year.
- If search_startups finds no matching startups or fewer than 3, call propose_web_search with a short
  topic for the missing area and 2-3 subtopics. Then say briefly what the database has, and that the
  user can start a web search with the card below. Never say that the web search already ran.
- Always answer in English, even if the question is in another language."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_startups",
            "description": (
                "Search the startup database. All given filters must match. "
                "Startups with an unknown founding year are left out when founded_since is used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Words that must appear in name, description or industry, e.g. 'robot hand' or 'tactile sensor'",
                    },
                    "countries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Full English country names, e.g. ['Germany', 'France']",
                    },
                    "city": {"type": "string", "description": "City name, e.g. 'Shanghai'"},
                    "founded_since": {"type": "integer", "description": "Only startups founded in this year or later"},
                    "limit": {"type": "integer", "description": "Maximum number of results (default 15)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_matches",
            "description": "Show the user a profile card for each startup from the search results that fits the question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "matches": {
                        "type": "array",
                        "description": "Best fit first",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Exact startup name from the search results"},
                                "score": {"type": "integer", "description": "Fit from 1 (weak) to 5 (perfect)"},
                                "why": {"type": "string", "description": "One short sentence why it fits, only facts from the results"},
                            },
                            "required": ["name", "score", "why"],
                        },
                    }
                },
                "required": ["matches"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_web_search",
            "description": (
                "Suggest a new web search for startups in an area that is missing or thin in the database. "
                "This does not search: the user sees a card and decides."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Short English topic, 2-4 words, e.g. 'lidar sensors'"},
                    "subtopics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-3 more specific English subtopics, e.g. ['automotive lidar', 'solid-state lidar']",
                    },
                },
                "required": ["topic"],
            },
        },
    },
]


RETRIES = 2  # for empty responses and hanging requests from the free NVIDIA tier
# Streamed: this is the longest wait for the next piece of the answer, so a hanging request
# is noticed after a minute, while a long answer that keeps coming is never cut off.
STREAM_TIMEOUT = 60


def _read_stream(stream) -> SimpleNamespace | None:
    """Put the streamed pieces back together: text plus tool calls (their arguments arrive in parts)."""
    content, calls, got_choices = [], {}, False
    for chunk in stream:
        if not chunk.choices:
            continue
        got_choices = True
        delta = chunk.choices[0].delta
        if delta.content:
            content.append(delta.content)
        for part in delta.tool_calls or []:
            call = calls.setdefault(part.index, {"id": None, "name": "", "arguments": ""})
            call["id"] = part.id or call["id"]
            if part.function and part.function.name:
                call["name"] += part.function.name
            if part.function and part.function.arguments:
                call["arguments"] += part.function.arguments
    if not got_choices:
        return None
    tool_calls = [
        SimpleNamespace(id=c["id"] or f"call_{i}", function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
        for i, c in sorted(calls.items())
    ]
    return SimpleNamespace(content="".join(content), tool_calls=tool_calls or None)


def _complete(messages: list[dict]) -> SimpleNamespace:
    """One model call. Retries empty responses and hanging requests, which the free tier sometimes has."""
    for attempt in range(RETRIES + 1):
        try:
            stream = llm.client().chat.completions.create(
                model=config.NVIDIA_CHAT_MODEL, messages=messages, tools=TOOLS, temperature=0, max_tokens=3000,
                stream=True, timeout=STREAM_TIMEOUT,
            )
            message = _read_stream(stream)
        except Exception as error:
            # APITimeoutError before the answer starts, or a ReadTimeout (httpx/httpx2) in the middle of the stream
            if "Timeout" not in type(error).__name__:
                raise
            logger.error("Model call hung and was retried (attempt %s): %s", attempt + 1, type(error).__name__)
            if attempt == RETRIES:
                raise
            continue
        if message is not None:
            return message
        logger.error("Empty response from the model (attempt %s)", attempt + 1)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("the model sent empty responses")


@dataclass
class Answer:
    text: str
    startups: list[Startup] = field(default_factory=list)  # everything the tool found, e.g. for a map
    web_search: dict | None = None  # {"topic", "subtopics"} if the agent suggests searching the web
    # The fitting startups picked by show_matches, best first: {"startup", "score", "why"}
    matches: list[dict] = field(default_factory=list)


def parse_matches(arguments: str, found: dict[str, Startup]) -> list[dict]:
    """Turn the show_matches call into a list of startups from the search results, with score and reason.

    Names the model made up (not in the results) are dropped.
    """
    try:
        raw = json.loads(arguments or "{}").get("matches") or []
    except (json.JSONDecodeError, AttributeError):
        return []
    by_name = {db.name_key(s): s for s in found.values()}
    matches, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        startup = by_name.get(db.name_key(Startup(name=str(item.get("name") or ""))))
        if startup is None or startup.name in seen:
            continue
        try:
            score = min(5, max(1, int(item.get("score") or 0)))
        except (TypeError, ValueError):
            score = None
        matches.append({"startup": startup, "score": score, "why": str(item.get("why") or "").strip()})
        seen.add(startup.name)
    # Best fit first, even if the model did not sort them (equal scores keep the model's order)
    return sorted(matches, key=lambda m: -(m["score"] or 0))


def parse_proposal(arguments: str) -> dict | None:
    """Check the agent's web search proposal: a short topic and at most 3 subtopics."""
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return None
    topic = str(args.get("topic") or "").strip()
    if not topic or len(topic) > 60:
        return None
    subtopics = [str(s).strip() for s in args.get("subtopics") or [] if str(s).strip()][:3]
    return {"topic": topic, "subtopics": subtopics}


def _as_tool_result(startups: list[Startup]) -> str:
    """Compact JSON for the model: only the fields it needs to answer."""
    keep = ["name", "summary", "industry", "city", "country", "founding_year", "yc_batch"]
    rows = [{k: v for k, v in vars(s).items() if k in keep and v not in (None, "")} for s in startups]
    for row in rows:  # shorter input = faster answers; the page shows the full text in the profile
        if len(row.get("summary", "")) > 220:
            row["summary"] = row["summary"][:217] + "…"
    return json.dumps({"count": len(rows), "startups": rows}, ensure_ascii=False)


def run_tool(conn, name: str, arguments: str) -> tuple[str, list[Startup]]:
    """Run one tool call from the model. Bad calls get an error message instead of crashing."""
    if name != "search_startups":
        return json.dumps({"error": f"unknown tool {name}"}), []
    try:
        args = json.loads(arguments or "{}")
        allowed = {"keyword", "countries", "city", "founded_since", "limit"}
        startups = db.search_startups(conn, **{k: v for k, v in args.items() if k in allowed})
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        logger.error("Bad tool call %s(%s): %s", name, arguments, error)
        return json.dumps({"error": f"invalid arguments: {error}"}), []
    return _as_tool_result(startups), startups


def summary_sentence(matches: list[dict]) -> str:
    """The short answer text when show_matches was used (saves one slow model call)."""
    names = [m["startup"].name for m in matches[:2]]
    best = f" The best fit is {names[0]}." if len(names) == 1 else f" The best fits are {names[0]} and {names[1]}."
    count = len(matches)
    return f"I found {count} potential match{'es' if count != 1 else ''}.{best}"


def ask(question: str, history: list[dict] | None = None, conn=None, on_step=None) -> Answer:
    """Answer one question. history holds earlier user/assistant messages and is extended.

    on_step(text) is called when the agent starts a new step, e.g. to show progress on the page.
    """
    conn = conn or db.connect()
    step = on_step or (lambda text: None)
    history = history if history is not None else []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": question}]
    found: dict[str, Startup] = {}
    proposal = None
    matches: list[dict] = []

    try:
        step("Understanding the question …")
        for _ in range(MAX_TOOL_ROUNDS + 1):
            message = _complete(messages)
            if not message.tool_calls:
                text = (message.content or "").strip() or "Sorry, I could not produce an answer."
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in message.tool_calls
                    ],
                }
            )
            for call in message.tool_calls:
                if call.function.name == "propose_web_search":
                    proposal = parse_proposal(call.function.arguments) or proposal
                    result = json.dumps(
                        {"status": "The card is shown to the user. The web search has NOT run yet."}
                        if proposal
                        else {"error": "invalid arguments: a short topic is required"}
                    )
                elif call.function.name == "show_matches":
                    matches = parse_matches(call.function.arguments, found) or matches
                    result = json.dumps(
                        {"status": f"{len(matches)} profiles are shown to the user. Now answer in 1-2 sentences."}
                        if matches
                        else {"error": "no valid matches: use exact names from the search results"}
                    )
                else:
                    result, startups = run_tool(conn, call.function.name, call.function.arguments)
                    found.update({db.startup_key(s): s for s in startups})
                    step(f"Found {len(found)} startups in the database, ranking the matches …")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            if matches:
                # The profiles say everything: write the short answer here instead of asking the model again
                text = (message.content or "").strip() or summary_sentence(matches)
                break
        else:
            text = "Sorry, I searched too many times without an answer. Please ask more specifically."
    except Exception as error:
        logger.exception("Chat request failed")
        text = f"Sorry, the AI service did not answer ({type(error).__name__}). Please try again."

    history += [{"role": "user", "content": question}, {"role": "assistant", "content": text}]
    return Answer(text=text, startups=list(found.values()), web_search=proposal, matches=matches)


def main() -> None:
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])).text)
        return

    print("Searchbrain chat. Ask about the stored startups, 'exit' to quit.\n")
    history: list[dict] = []
    conn = db.connect()
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit"}:
            break
        if question:
            print(f"\nSearchbrain: {ask(question, history, conn).text}\n")


if __name__ == "__main__":
    main()
