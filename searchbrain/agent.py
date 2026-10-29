"""Step 8: chat agent that answers questions from the stored startups.

The NVIDIA model gets one tool, search_startups, which queries the SQLite database.
It decides itself when and how to search, and answers only from the results.

Run with:  .venv\\Scripts\\python.exe -m searchbrain.agent
      or:  .venv\\Scripts\\python.exe -m searchbrain.agent "Which startups build robot hands in China?"
"""

import json
import sys
import time
from dataclasses import dataclass, field

from searchbrain import config, db, llm
from searchbrain.log import get_logger
from searchbrain.models import Startup

logger = get_logger(__name__)

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """Reasoning: low
You answer questions about startups stored in the Searchbrain database.
Rules:
- Use the search_startups tool before answering any question about startups.
- Answer only with facts from the tool results. Never use outside knowledge about companies.
- For every startup you mention, give its source link, and its website if known.
- If nothing is found, you may search once more with fewer or broader words.
  If there is still nothing, say that the database has no matching startups.
- For regions like Europe or Asia, pass the list of countries in that region.
- If the question only asks about a place or a founding year (no technology), search without keyword.
  Words like "startup", "company" or the general topic of the database are not keywords.
- If a value (city, founding year) is unknown, say it is unknown. Do not guess.
- If search_startups finds no matching startups or fewer than 3, call propose_web_search with a short
  topic for the missing area and 2-3 subtopics. Then say briefly what the database has, and that the
  user can start a web search with the card below. Never say that the web search already ran.
- Always answer in English, even if the question is in another language.
- Keep the answer short and use a list for several startups."""

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


EMPTY_RESPONSE_RETRIES = 2


def _complete(messages: list[dict]):
    """One model call. The free NVIDIA tier sometimes sends a response without choices: retry those."""
    for attempt in range(EMPTY_RESPONSE_RETRIES + 1):
        response = llm.client().chat.completions.create(
            model=config.NVIDIA_MODEL, messages=messages, tools=TOOLS, temperature=0, max_tokens=2000
        )
        if response.choices:
            return response.choices[0].message
        logger.error("Empty response from the model (attempt %s): %s", attempt + 1, str(response.model_extra)[:300])
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("the model sent empty responses")


@dataclass
class Answer:
    text: str
    startups: list[Startup] = field(default_factory=list)  # everything the tool found, e.g. for a map
    web_search: dict | None = None  # {"topic", "subtopics"} if the agent suggests searching the web


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
    keep = ["name", "summary", "industry", "city", "country", "founding_year", "link", "source_url"]
    rows = [{k: v for k, v in vars(s).items() if k in keep and v not in (None, "")} for s in startups]
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


def ask(question: str, history: list[dict] | None = None, conn=None) -> Answer:
    """Answer one question. history holds earlier user/assistant messages and is extended."""
    conn = conn or db.connect()
    history = history if history is not None else []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": question}]
    found: dict[str, Startup] = {}
    proposal = None

    try:
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
                else:
                    result, startups = run_tool(conn, call.function.name, call.function.arguments)
                    found.update({db.startup_key(s): s for s in startups})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        else:
            text = "Sorry, I searched too many times without an answer. Please ask more specifically."
    except Exception as error:
        logger.exception("Chat request failed")
        text = f"Sorry, the AI service did not answer ({type(error).__name__}). Please try again."

    history += [{"role": "user", "content": question}, {"role": "assistant", "content": text}]
    return Answer(text=text, startups=list(found.values()), web_search=proposal)


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
