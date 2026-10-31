"""Searchbrain web page, in the calm chat-first style of Harmonic Scout.

Views: home (greeting, ask field, world map, Scouts), chat, scout, browse, charts, favorites.

Start with:  .venv\\Scripts\\streamlit.exe run app.py
"""

import re
from datetime import date, datetime
from html import escape

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from searchbrain import agent, config, db, storage
from searchbrain.collect import collect
from searchbrain.log import get_logger
from searchbrain.models import Startup
from searchbrain.worldmap import build_map, safe_url

logger = get_logger("searchbrain.app")

st.set_page_config(page_title="Searchbrain", page_icon="🧠", layout="wide")

st.markdown(
    """
    <style>
    .block-container {max-width: 1150px; padding-top: 2rem;}
    header[data-testid="stHeader"] {background: transparent;}

    /* sidebar: white panel with flat text rows, like the Scout sidebar */
    [data-testid="stSidebar"] {background: #FFFFFF; border-right: 1px solid #ECECEC;}
    [data-testid="stSidebar"] .stButton button {
        background: transparent; border: none; box-shadow: none; color: #374151; font-weight: 400;
        justify-content: flex-start; padding: .28rem .6rem; min-height: 0; width: 100%;}
    [data-testid="stSidebar"] .stButton button:hover {background: #F3F4F6; color: #111827;}
    [data-testid="stSidebar"] .stButton button[kind="primary"] {background: #ECECEC; color: #111827;}
    /* long chat titles must not push the sidebar content out to the left */
    [data-testid="stSidebarContent"] {overflow-x: hidden;}
    [data-testid="stSidebar"] .stButton, [data-testid="stSidebar"] .stButton button {max-width: 100%; min-width: 0; overflow: hidden;}
    [data-testid="stSidebar"] .stButton button > div {width: 100%; min-width: 0; justify-content: flex-start;}
    [data-testid="stSidebar"] .stButton button p {text-align: left; white-space: nowrap; overflow: hidden;
                                                  text-overflow: ellipsis; margin: 0; min-width: 0;}
    .brand {font-weight: 600; font-size: 1.15rem; margin: 0 0 .8rem .6rem;}
    .side-label {color: #9CA3AF; font-size: .78rem; margin: .9rem 0 .1rem .6rem;}

    /* home */
    .greeting {text-align: center; margin: 6vh 0 1.4rem;}
    .greeting h1 {font-weight: 500; font-size: 2.5rem; color: #111827; padding: 0;}
    .greeting p {color: #6B7280; font-size: 1.3rem; margin-top: .25rem;}
    .st-key-home_input {max-width: 760px; margin: 0 auto;}
    [data-testid="stChatInput"] {background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 18px;
                                 box-shadow: 0 6px 24px rgba(17, 24, 39, .06);}
    .section-label {color: #6B7280; font-size: .92rem; margin: 2rem 0 .6rem; display: flex; justify-content: space-between;}

    /* white rounded cards for every bordered container */
    [data-testid="stVerticalBlockBorderWrapper"] {background: #FFFFFF; border-radius: 16px;}

    /* chat: grey bubble for the question, small grey chips for startups */
    [class*="st-key-user_msg_"] {background: #EBEBEB; border-radius: 16px; padding: .7rem 1rem;
                                 margin-left: auto; max-width: 75%;}
    [class*="st-key-chip_"] button {border-radius: 999px; background: #F3F4F6; border: 1px solid #E5E7EB;
                                    padding: .05rem .75rem; min-height: 0; font-size: .85rem;}
    .chat-header {color: #374151; font-weight: 500;}
    .muted {color: #9CA3AF; font-weight: 400;}
    .badge-yc {background: #FFEDD5; color: #C2410C; border-radius: 6px; padding: .05rem .4rem;
               font-size: .72rem; margin-left: .4rem;}
    .badge-new {background: #DBEAFE; color: #1D4ED8; border-radius: 6px; padding: .05rem .4rem;
                font-size: .72rem; margin-left: .4rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

YEARS = [0, *range(date.today().year, 1999, -1)]  # 0 = any year
EMPTY_FILTERS = {"area": "", "keyword": "", "countries": [], "since": 0, "located": False, "yc": False}


# ---------- helpers ----------


def md_escape(text: str) -> str:
    """Show web text as plain text, so it cannot add links or formatting."""
    return re.sub(r"([\\`*_\[\]<>#|~])", r"\\\1", text)


def place(startup: Startup) -> str:
    return ", ".join(filter(None, [startup.city, startup.country])) or "location unknown"


def normalize_filters(raw: dict) -> dict:
    """Also reads Scouts saved by the first page version (keys with f_ prefix)."""
    return {key: raw.get(key, raw.get(f"f_{key}", default)) for key, default in EMPTY_FILTERS.items()}


def current_filters() -> dict:
    return {key: st.session_state[f"f_{key}"] for key in EMPTY_FILTERS}


def matches(startup: Startup, filters: dict) -> bool:
    if filters["area"] and startup.topic != filters["area"]:
        return False
    text = " ".join(filter(None, [startup.name, startup.description, startup.summary, startup.industry])).lower()
    if any(word not in text for word in db.keyword_stems(filters["keyword"])):
        return False
    if filters["countries"] and startup.country not in filters["countries"]:
        return False
    if filters["since"] and (startup.founding_year or 0) < filters["since"]:
        return False
    if filters["located"] and startup.location_precision not in ("city", "country"):
        return False
    if filters["yc"] and not startup.yc_batch:
        return False
    return True


def filter_summary(filters: dict) -> str:
    parts = [
        f"area {filters['area']}" if filters["area"] else None,
        f"“{filters['keyword']}”" if filters["keyword"] else None,
        ", ".join(filters["countries"]) or None,
        f"founded {filters['since']}+" if filters["since"] else None,
        "with location" if filters["located"] else None,
        "YC only" if filters["yc"] else None,
    ]
    return " · ".join(p for p in parts if p) or "all startups"


def greeting() -> str:
    hour = datetime.now().hour
    text = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    return f"{text}, {config.USER_NAME}" if config.USER_NAME else text


def section_label(left: str, right: str = "") -> None:
    st.markdown(f'<div class="section-label"><span>{left}</span><span>{right}</span></div>', unsafe_allow_html=True)


def show_map(startups: list[Startup], key: str, height: int) -> None:
    st_folium(build_map(startups), height=height, use_container_width=True, returned_objects=[], key=key)


# ---------- callbacks (they run before the next page run, so they open their own connection) ----------


def go(view: str, **state) -> None:
    st.session_state.view = view
    st.session_state.update(state)


def new_chat() -> None:
    go("home", chat_id=None, pending_question=None)


def start_chat(question: str) -> None:
    chat_id = storage.create_chat(db.connect(), question)
    go("chat", chat_id=chat_id, pending_question=question)


def open_chat(chat_id: int) -> None:
    go("chat", chat_id=chat_id, pending_question=None)


def open_scout(name: str, new_keys: list[str]) -> None:
    st.session_state.scout_new[name] = set(new_keys)  # keep the "New" marks while the Scout is open
    storage.mark_search_seen(db.connect(), name)
    go("scout", scout=name)


def save_scout() -> None:
    name = st.session_state.get("scout_name", "").strip()
    if name:
        storage.save_search(db.connect(), name, current_filters())
        st.session_state.scout_name = ""
        st.session_state.scout_new[name] = set()
        go("scout", scout=name)


def delete_scout(name: str) -> None:
    storage.delete_search(db.connect(), name)
    new_chat()


def delete_current_chat(chat_id: int) -> None:
    storage.delete_chat(db.connect(), chat_id)
    new_chat()


def toggle_favorite(key: str, is_favorite: bool) -> None:
    conn = db.connect()
    (storage.remove_favorite if is_favorite else storage.add_favorite)(conn, key)


def show_startup(key: str) -> None:
    st.session_state.detail = key


def reset_filters() -> None:
    for key, default in EMPTY_FILTERS.items():
        st.session_state[f"f_{key}"] = default


def split_subtopics(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()][:3]


def request_web_search(chat_id: int, index: int, yc_only: bool = False) -> None:
    """The user confirmed the proposal card: the search runs in the next page run, inside the chat."""
    topic = st.session_state.get(f"ws_topic_{chat_id}_{index}", "").strip()
    if topic:
        subtopics = split_subtopics(st.session_state.get(f"ws_subs_{chat_id}_{index}", ""))
        if yc_only:
            sources = ("yc",)
        else:
            sources = ("yc", "web") if st.session_state.get(f"ws_yc_{chat_id}_{index}", True) else ("web",)
        st.session_state.run_search = {"chat_id": chat_id, "index": index, "topic": topic, "subtopics": subtopics, "sources": sources}


def dismiss_proposal(chat_id: int, index: int) -> None:
    conn = db.connect()
    chat = storage.get_chat(conn, chat_id)
    chat["messages"][index]["web_search"]["status"] = "dismissed"
    storage.save_messages(conn, chat_id, chat["messages"])


# ---------- data and state ----------

conn = db.connect()
records = db.all_startups_with_keys(conn)
by_key = dict(records)
all_startups = [s for _, s in records]
created = db.created_times(conn)
favorites = storage.favorite_keys(conn)
scouts = storage.saved_searches(conn)
countries_available = sorted({s.country for s in all_startups if s.country})
areas_available = db.topics(conn)
missing_keys = config.missing_keys()
chat_disabled = "NVIDIA_API_KEY" in missing_keys
search_disabled = bool(missing_keys)

for key, value in {"view": "home", "chat_id": None, "pending_question": None, "scout": None, "scout_new": {}}.items():
    st.session_state.setdefault(key, value)
for key, default in EMPTY_FILTERS.items():
    st.session_state.setdefault(f"f_{key}", default)
# Values that are no longer in the data cannot stay selected in the filter widgets
st.session_state.f_countries = [c for c in st.session_state.f_countries if c in countries_available]
if st.session_state.f_area not in areas_available:
    st.session_state.f_area = ""


def scout_new_keys(scout: dict) -> list[str]:
    filters = normalize_filters(scout["filters"])
    return [key for key, s in records if matches(s, filters) and created.get(key, "") > scout["last_seen_at"]]


# ---------- detail dialog ----------


@st.dialog("Startup", width="large")
def startup_dialog(key: str) -> None:
    startup = by_key.get(key)
    if startup is None:
        st.write("This startup is no longer in the database.")
        return
    is_favorite = key in favorites
    st.subheader(startup.name)
    yc_note = f" · Y Combinator {startup.yc_batch}" if startup.yc_batch else ""
    st.caption(
        f"{md_escape(place(startup))} · founded {startup.founding_year or 'unknown'}{yc_note} · {md_escape(startup.industry or 'industry unknown')}"
    )
    st.markdown(md_escape(startup.summary or startup.description or "No description yet."))
    if startup.description and startup.summary and startup.description != startup.summary:
        with st.expander("Original description from the source"):
            st.markdown(md_escape(startup.description))
    buttons = st.columns(3)
    if website := safe_url(startup.link):
        buttons[0].link_button("🌐 Website", website)
    if source := safe_url(startup.source_url):
        buttons[1].link_button("📰 Source", source)
    if buttons[2].button("★ Remove favorite" if is_favorite else "☆ Add to favorites", key=f"dialog_fav_{key}"):
        toggle_favorite(key, is_favorite)
        st.rerun()


def startup_card(key: str, startup: Startup, prefix: str, is_new: bool = False) -> None:
    with st.container(border=True):
        badge = f'<span class="badge-yc">YC {escape(startup.yc_batch)}</span>' if startup.yc_batch else ""
        badge += '<span class="badge-new">New</span>' if is_new else ""
        star = " ⭐" if key in favorites else ""
        st.markdown(f"<b>{escape(startup.name)}</b>{star}{badge}", unsafe_allow_html=True)
        st.caption(f"{md_escape(place(startup))} · {startup.founding_year or 'year unknown'}")
        st.markdown(md_escape(startup.summary or startup.description or ""))
        st.button("Details", key=f"chip_{prefix}_{key}", on_click=show_startup, args=(key,))


# ---------- sidebar ----------

with st.sidebar:
    st.markdown('<div class="brand">🧠 Searchbrain</div>', unsafe_allow_html=True)
    view = st.session_state.view
    st.button("🏠  Home", key="nav_home", on_click=new_chat, type="primary" if view == "home" else "secondary")
    for name, label_text in [("browse", "🔎  Browse startups"), ("charts", "📊  Charts"), ("favorites", "⭐  Favorites")]:
        st.button(label_text, key=f"nav_{name}", on_click=go, args=(name,), type="primary" if view == name else "secondary")
    st.button("＋  New chat", key="nav_new_chat", on_click=new_chat)

    st.divider()
    label, plus = st.columns([5, 1])
    label.markdown('<div class="side-label">Scouts</div>', unsafe_allow_html=True)
    plus.button("＋", key="nav_add_scout", on_click=go, args=("browse",), help="Set filters in Browse, then save them as a Scout")
    if not scouts:
        st.caption("  No Scouts yet")
    for scout in scouts:
        new_keys = scout_new_keys(scout)
        active = view == "scout" and st.session_state.scout == scout["name"]
        st.button(
            f"◉  {scout['name']}" + (f"  ·  {len(new_keys)} new" if new_keys and not active else ""),
            key=f"scout_{scout['name']}",
            on_click=open_scout,
            args=(scout["name"], new_keys),
            type="primary" if active else "secondary",
        )

    st.markdown('<div class="side-label">Chats</div>', unsafe_allow_html=True)
    chats = storage.list_chats(conn)
    if not chats:
        st.caption("  No chats yet")
    for chat in chats:
        active = view == "chat" and st.session_state.chat_id == chat["id"]
        st.button(chat["title"], key=f"chat_{chat['id']}", on_click=open_chat, args=(chat["id"],), type="primary" if active else "secondary")


# ---------- web search for a new area ----------


def run_web_search(topic: str, subtopics: list[str], sources: tuple[str, ...] = ("yc", "web")) -> dict | None:
    """Collect startups for a new area with a progress bar, then create a Scout for it.

    Returns a small report, or None if the search failed.
    """
    where = " and ".join({"yc": "Y Combinator", "web": "the web"}[s] for s in sources)
    with st.status(f"Searching {where} for “{topic}” …", expanded=True) as status:
        bar = st.progress(0.0, text="Searching the web …" if "web" in sources else "Searching the YC directory and placing startups on the map …")

        def show_progress(number: int, count: int, state: str) -> None:
            bar.progress(number / count, text=f"AI extraction: batch {number} of {count} ({state})")

        try:
            report = collect(topic, subtopics, on_progress=show_progress, sources=sources)
        except Exception as error:
            logger.exception("Web search from the page failed")
            status.update(label=f"Web search failed: {type(error).__name__}. Details are in logs/searchbrain.log", state="error")
            return None
        storage.save_search(conn, topic, {**EMPTY_FILTERS, "area": topic})
        summary = {
            "yc": report["yc"],
            "extracted": report["extracted"],
            "new": report["new"],
            "updated": report["updated"],
            "failed_batches": report["batches"]["failed"],
        }
        status.update(label=f"Found {summary['extracted']} startups ({summary['new']} new) · Scout “{topic}” created", state="complete")
        return summary


def web_search_note(summary: dict) -> str:
    text = f"{summary['extracted']} startups found ({summary['new']} new, {summary['updated']} already known)"
    if summary.get("yc"):
        text += f" · {summary['yc']} of them from Y Combinator"
    if summary.get("failed_batches"):
        text += f" · {summary['failed_batches']} parts failed, search again later to fill them in"
    return text


# ---------- views ----------


def view_home() -> None:
    st.markdown(
        f'<div class="greeting"><h1>{escape(greeting())}</h1><p>What are we looking for today?</p></div>', unsafe_allow_html=True
    )
    if missing_keys:
        st.warning(f"Missing in .env: {', '.join(missing_keys)}. Chat and collecting need these keys.")
    with st.container(key="home_input"):
        question = st.chat_input(
            "Ask Searchbrain… e.g. “Find startups in battery recycling”", key="home_question", disabled=chat_disabled
        )
    if question:
        start_chat(question)
        st.rerun()

    section_label("🗺️  Where the startups are")
    pills_col, stats_col = st.columns([3, 2], vertical_alignment="center")
    area = None
    if len(areas_available) > 1:
        area = pills_col.pills("Area", ["All areas", *areas_available], default="All areas", key="home_area", label_visibility="collapsed")
    shown = [s for s in all_startups if area in (None, "All areas") or s.topic == area]
    on_map = sum(s.location_precision in ("city", "country") for s in shown)
    countries = len({s.country for s in shown if s.country})
    stats_col.caption(f"{len(shown)} startups · {countries} countries · {on_map} on the map")
    with st.container(border=True):
        show_map(shown, key="home_map", height=480)

    section_label("◉  Your Scouts", "Saved searches that show what is new")
    cards = st.columns(4)
    for column, scout in zip(cards, scouts[:3]):
        new_keys = scout_new_keys(scout)
        with column.container(border=True):
            st.markdown(f"**{md_escape(scout['name'])}**")
            st.caption(md_escape(filter_summary(normalize_filters(scout["filters"]))))
            st.button(
                f"{len(new_keys)} new · Open" if new_keys else "Open",
                key=f"home_scout_{scout['name']}",
                on_click=open_scout,
                args=(scout["name"], new_keys),
            )
    with cards[min(len(scouts), 3)].container(border=True):
        st.markdown("**＋ New Scout**")
        st.caption("Filter startups in Browse and save the search")
        st.button("Create", key="home_new_scout", on_click=go, args=("browse",))


def proposal_card(chat_id: int, index: int, proposal: dict) -> None:
    with st.container(border=True, key=f"proposal_{chat_id}_{index}"):
        st.markdown("**🔍 Search the web for a new area?**")
        if proposal.get("error"):
            st.warning("The last web search failed. Details are in logs/searchbrain.log. You can try again.")
        topic_col, subs_col = st.columns([2, 3])
        topic_col.text_input("Area", value=proposal["topic"], key=f"ws_topic_{chat_id}_{index}")
        subs_col.text_input(
            "Subtopics (comma separated)", value=", ".join(proposal.get("subtopics", [])), key=f"ws_subs_{chat_id}_{index}"
        )
        st.checkbox("Also search the Y Combinator directory (free, takes seconds)", value=True, key=f"ws_yc_{chat_id}_{index}")
        searches = 3 + len(split_subtopics(st.session_state.get(f"ws_subs_{chat_id}_{index}", "")))
        st.caption(f"About {searches} web searches (free Tavily credits) · takes a few minutes · creates a Scout for the area")
        start, yc_only, later, _ = st.columns([1.3, 1.6, 1, 2.5])
        start.button(
            "Start search", key=f"ws_start_{chat_id}_{index}", type="primary",
            on_click=request_web_search, args=(chat_id, index), disabled=search_disabled,
        )
        yc_only.button(
            "YC only (instant)", key=f"ws_yc_only_{chat_id}_{index}",
            on_click=request_web_search, args=(chat_id, index, True), disabled="NOMINATIM_USER_AGENT" in missing_keys,
        )
        later.button("Not now", key=f"ws_later_{chat_id}_{index}", on_click=dismiss_proposal, args=(chat_id, index))


def md_link(label: str, url: str | None) -> str | None:
    safe = safe_url(url)
    return f"[{label}]({safe.replace(' ', '%20').replace(')', '%29')})" if safe else None


def render_profiles(chat_id: int, index: int, matches: list[dict]) -> None:
    """One profile per fitting startup, like the "Detailed Profiles" in Harmonic Scout."""
    st.markdown(f"#### Potential matches ({len(matches)})")
    for number, match in enumerate(matches, start=1):
        startup = by_key[match["key"]]
        head = st.columns([0.3, 3, 4], vertical_alignment="center")
        head[0].markdown(f"**{number}.**")
        head[1].button(startup.name, key=f"chip_{chat_id}_{index}_{match['key']}", on_click=show_startup, args=(match["key"],))
        if match.get("score"):
            head[2].markdown(f"— **Fit: {match['score']}/5** ⭐")

        facts = [f"**Location:** {md_escape(place(startup))}", f"**Founded:** {startup.founding_year or 'unknown'}"]
        if startup.yc_batch:
            facts.append(f"**YC:** {startup.yc_batch}")
        if startup.industry:
            facts.append(f"**Industry:** {md_escape(startup.industry)}")
        lines = [" · ".join(facts), f"**What they do:** {md_escape(startup.summary or startup.description or 'no description yet')}"]
        if match.get("why"):
            lines.append(f"**Why it fits:** {md_escape(match['why'])}")
        links = " · ".join(filter(None, [md_link("Website", startup.link), md_link("Source", startup.source_url)]))
        if links:
            lines.append(links)
        st.markdown("\n\n".join(lines))
        if number < len(matches):
            st.divider()


def render_answer(chat_id: int, index: int, message: dict, latest: bool) -> None:
    st.markdown(message["content"])
    proposal = message.get("web_search")
    if proposal and proposal["status"] == "proposed" and latest:
        proposal_card(chat_id, index, proposal)
    elif proposal and proposal["status"] == "done":
        note, open_col = st.columns([5, 1.3], vertical_alignment="center")
        note.caption(f"✅ Web search for “{proposal['topic']}”: {web_search_note(proposal['report'])}")
        open_col.button("Open Scout", key=f"ws_scout_{chat_id}_{index}", on_click=open_scout, args=(proposal["topic"], []))

    matches = [m for m in message.get("matches", []) if m.get("key") in by_key]
    if matches:
        render_profiles(chat_id, index, matches)
        keys = [m["key"] for m in matches]
    else:
        # Answers from before the profiles existed: the startups as chips
        keys = [key for key in message.get("found", []) if key in by_key]
        if not keys:
            return
        st.caption("Found in the database")
        for row_start in range(0, len(keys), 4):
            for column, key in zip(st.columns(4), keys[row_start : row_start + 4]):
                column.button(by_key[key].name, key=f"chip_{chat_id}_{index}_{key}", on_click=show_startup, args=(key,))
    located = [by_key[k] for k in keys if by_key[k].location_precision in ("city", "country")]
    if located:
        with st.expander(f"🗺️  Show {len(located)} of them on the map", expanded=latest):
            show_map(located, key=f"chat_map_{chat_id}_{index}", height=360)


def view_chat() -> None:
    chat = storage.get_chat(conn, st.session_state.chat_id) if st.session_state.chat_id else None
    if chat is None:
        new_chat()
        st.rerun()
    messages = chat["messages"]

    back, title, delete = st.columns([1, 8, 1])
    back.button("←", key="chat_back", on_click=new_chat, help="Back to the start page")
    day = "Today" if chat["created_at"][:10] == datetime.now().strftime("%Y-%m-%d") else chat["created_at"][:10]
    title.markdown(f'<div class="chat-header">{escape(chat["title"])} <span class="muted">· {day}</span></div>', unsafe_allow_html=True)
    delete.button("🗑", key="chat_delete", on_click=delete_current_chat, args=(chat["id"],), help="Delete this chat")
    st.divider()

    last_answer = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    for index, message in enumerate(messages):
        if message["role"] == "user":
            with st.container(key=f"user_msg_{index}"):
                st.markdown(md_escape(message["content"]))
        else:
            render_answer(chat["id"], index, message, latest=index == last_answer and not st.session_state.pending_question)

    job = st.session_state.get("run_search")
    if job and job["chat_id"] == chat["id"]:
        st.session_state.run_search = None
        summary = run_web_search(job["topic"], job["subtopics"], job.get("sources", ("yc", "web")))
        proposal = messages[job["index"]]["web_search"]
        if summary is None:
            proposal.update(topic=job["topic"], subtopics=job["subtopics"], error=True)
        else:
            proposal.update(status="done", topic=job["topic"], subtopics=job["subtopics"], report=summary, error=False)
            # Ask the original question again, now with the new startups in the database
            st.session_state.pending_question = messages[job["index"] - 1]["content"]
        storage.save_messages(conn, chat["id"], messages)
        st.rerun()

    question = st.session_state.pending_question
    if question:
        with st.container(key=f"user_msg_{len(messages)}"):
            st.markdown(md_escape(question))
        with st.status("Searching the database …") as status:
            history = [{"role": m["role"], "content": m["content"]} for m in messages]
            answer = agent.ask(question, history, conn, on_step=lambda text: status.update(label=text))
            status.update(label="Done", state="complete")
        found = [key for s in answer.startups if (key := db.existing_key(conn, s))]
        reply = {"role": "assistant", "content": answer.text, "found": found}
        if answer.matches:
            reply["matches"] = [
                {"key": key, "score": m["score"], "why": m["why"]} for m in answer.matches if (key := db.existing_key(conn, m["startup"]))
            ]
        if answer.web_search:
            reply["web_search"] = {**answer.web_search, "status": "proposed"}
        messages += [{"role": "user", "content": question}, reply]
        storage.save_messages(conn, chat["id"], messages)
        st.session_state.pending_question = None
        st.rerun()

    follow_up = st.chat_input("Ask a follow-up…", key="follow_up", disabled=chat_disabled)
    if follow_up:
        st.session_state.pending_question = follow_up
        st.rerun()


def view_scout() -> None:
    scout = next((s for s in scouts if s["name"] == st.session_state.scout), None)
    if scout is None:
        new_chat()
        st.rerun()
    filters = normalize_filters(scout["filters"])
    new_keys = st.session_state.scout_new.get(scout["name"], set())
    found = [(key, s) for key, s in records if matches(s, filters)]
    found.sort(key=lambda item: item[0] not in new_keys)  # new startups first

    back, title, delete = st.columns([1, 8, 1])
    back.button("←", key="scout_back", on_click=new_chat, help="Back to the start page")
    title.markdown(
        f'<div class="chat-header">◉ {escape(scout["name"])} <span class="muted">· {escape(filter_summary(filters))}</span></div>',
        unsafe_allow_html=True,
    )
    delete.button("🗑", key="scout_delete", on_click=delete_scout, args=(scout["name"],), help="Delete this Scout")
    st.caption(f"{len(found)} startups · {len(new_keys)} new since your last visit")
    st.divider()

    left, right = st.columns([1, 1])
    with left:
        if not found:
            st.info("No startups match this Scout yet. Collect a new topic in the sidebar.")
        for key, startup in found:
            startup_card(key, startup, prefix="scout", is_new=key in new_keys)
    with right, st.container(border=True):
        show_map([s for _, s in found], key="scout_map", height=560)


def view_browse() -> None:
    st.markdown("### Browse startups")
    columns = st.columns([2.2, 3, 3, 2, 2], vertical_alignment="bottom")
    columns[0].selectbox("Area", ["", *areas_available], key="f_area", format_func=lambda a: a or "All areas")
    columns[1].text_input("Search", key="f_keyword", placeholder="Name, description, industry …")
    columns[2].multiselect("Countries", countries_available, key="f_countries", placeholder="All countries")
    columns[3].selectbox("Founded in or after", YEARS, key="f_since", format_func=lambda y: "Any year" if y == 0 else str(y))
    columns[4].checkbox("Only with location", key="f_located")
    columns[4].checkbox("Only YC companies", key="f_yc")

    filters = current_filters()
    found = [(key, s) for key, s in records if matches(s, filters)]
    info, reset, csv, save = st.columns([4, 1.4, 1.6, 1.8])
    info.caption(f"{len(found)} of {len(records)} startups")
    reset.button("Reset filters", key="reset_filters", on_click=reset_filters)
    export = pd.DataFrame([vars(s) for _, s in found])
    csv.download_button("⬇️ CSV", export.to_csv(index=False).encode("utf-8"), file_name="searchbrain_startups.csv", mime="text/csv")
    with save.popover("◉ Save as Scout"):
        st.text_input("Name", key="scout_name", placeholder="e.g. Hands in China")
        st.button("Save Scout", key="save_scout", on_click=save_scout, disabled=not st.session_state.get("scout_name"))

    if not found:
        st.info("No startups match the filters.")
        return
    table = pd.DataFrame(
        [
            {
                "★": "⭐" if key in favorites else "",
                "Name": s.name,
                "Industry": s.industry,
                "City": s.city,
                "Country": s.country,
                "Founded": s.founding_year,
                "YC": s.yc_batch,
                "Website": safe_url(s.link),
                "Source": safe_url(s.source_url),
            }
            for key, s in found
        ]
    )
    table["Founded"] = table["Founded"].astype("Int64")
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="startup_table",
        column_config={
            "★": st.column_config.TextColumn("★", width="small"),
            "Founded": st.column_config.NumberColumn(format="%d"),
            "Website": st.column_config.LinkColumn(display_text="Website"),
            "Source": st.column_config.LinkColumn(display_text="Source"),
        },
    )
    if event.selection.rows:
        key, startup = found[event.selection.rows[0]]
        st.button(f"Open details for {startup.name}", key=f"chip_browse_{key}", on_click=show_startup, args=(key,))
    else:
        st.caption("Click a row to open its details.")
    with st.container(border=True):
        show_map([s for _, s in found], key="browse_map", height=420)


def bar_chart(counts: pd.Series, label: str, color: str) -> alt.Chart:
    data = counts.rename_axis(label).reset_index(name="Startups")
    return (
        alt.Chart(data)
        .mark_bar(color=color, cornerRadiusEnd=4)
        .encode(x=alt.X("Startups:Q", title="Startups"), y=alt.Y(f"{label}:N", sort="-x", title=None), tooltip=[label, "Startups"])
        .properties(height=max(120, 28 * len(data)))
    )


def view_charts() -> None:
    st.markdown("### Charts")
    if not all_startups:
        st.info("No startups stored yet.")
        return
    frame = pd.DataFrame([vars(s) for s in all_startups])
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("**By country**")
        st.altair_chart(bar_chart(frame["country"].fillna("unknown").value_counts().head(12), "Country", "#2563EB"), width="stretch")
    with right, st.container(border=True):
        st.markdown("**By industry**")
        st.altair_chart(bar_chart(frame["industry"].fillna("unknown").value_counts().head(12), "Industry", "#6B7280"), width="stretch")
    with st.container(border=True):
        st.markdown("**By founding year**")
        years = frame["founding_year"].dropna().astype(int)
        if years.empty:
            st.caption("No founding years known yet.")
        else:
            year_data = years.value_counts().sort_index().rename_axis("Year").reset_index(name="Startups")
            year_data["Year"] = year_data["Year"].astype(str)
            st.altair_chart(
                alt.Chart(year_data).mark_bar(color="#2563EB", cornerRadiusEnd=4).encode(x="Year:O", y="Startups:Q", tooltip=["Year", "Startups"]),
                width="stretch",
            )
            st.caption(f"{frame['founding_year'].isna().sum()} startups with unknown founding year are not shown.")


def view_favorites() -> None:
    st.markdown("### ⭐ Favorites")
    favorite_records = [(key, s) for key, s in records if key in favorites]
    if not favorite_records:
        st.caption("No favorites yet. Open a startup's details and click “Add to favorites”.")
        return
    left, right = st.columns([1, 1])
    with left:
        for key, startup in favorite_records:
            startup_card(key, startup, prefix="fav")
    with right, st.container(border=True):
        show_map([s for _, s in favorite_records], key="favorites_map", height=480)


VIEWS = {"home": view_home, "chat": view_chat, "scout": view_scout, "browse": view_browse, "charts": view_charts, "favorites": view_favorites}
# st.empty() first replaces everything from the last page run, so e.g. the start page does not stay
# visible (faded) while a chat answer is loading. Then the current view is drawn into that place.
with st.empty().container():
    VIEWS.get(st.session_state.view, view_home)()

if detail := st.session_state.pop("detail", None):
    startup_dialog(detail)
