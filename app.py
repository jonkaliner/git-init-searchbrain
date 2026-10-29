"""Searchbrain web page, in the calm chat-first style of Harmonic Scout.

Views: home (greeting, ask field, suggestions, world map, Scouts), chat, scout, browse, charts, favorites.

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
    [data-testid="stSidebar"] .stButton button p {text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
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
    .st-key-suggestions .stButton button {
        background: #EBEBEB; border: none; border-radius: 16px; min-height: 118px; width: 100%; color: #111827;
        justify-content: flex-start; align-items: flex-end; padding: 1rem 1.1rem;}
    .st-key-suggestions .stButton button:hover {background: #E2E2E2;}
    .st-key-suggestions .stButton button p {text-align: left;}

    /* white rounded cards for every bordered container */
    [data-testid="stVerticalBlockBorderWrapper"] {background: #FFFFFF; border-radius: 16px;}

    /* chat: grey bubble for the question, small grey chips for startups */
    [class*="st-key-user_msg_"] {background: #EBEBEB; border-radius: 16px; padding: .7rem 1rem;
                                 margin-left: auto; max-width: 75%;}
    [class*="st-key-chip_"] button {border-radius: 999px; background: #F3F4F6; border: 1px solid #E5E7EB;
                                    padding: .05rem .75rem; min-height: 0; font-size: .85rem;}
    .chat-header {color: #374151; font-weight: 500;}
    .muted {color: #9CA3AF; font-weight: 400;}
    .badge-new {background: #DBEAFE; color: #1D4ED8; border-radius: 6px; padding: .05rem .4rem;
                font-size: .72rem; margin-left: .4rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

YEARS = [0, *range(date.today().year, 1999, -1)]  # 0 = any year
EMPTY_FILTERS = {"keyword": "", "countries": [], "since": 0, "located": False}
SUGGESTIONS = [
    ("✋", "Robot hands in China", "Which startups build robot hands in China?"),
    ("🇪🇺", "Humanoids in Europe", "Which humanoid robotics startups are in Europe?"),
    ("🌱", "Founded 2023 or later", "Which startups were founded in 2023 or later?"),
]


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
    text = " ".join(filter(None, [startup.name, startup.description, startup.summary, startup.industry])).lower()
    if any(word not in text for word in filters["keyword"].lower().split()):
        return False
    if filters["countries"] and startup.country not in filters["countries"]:
        return False
    if filters["since"] and (startup.founding_year or 0) < filters["since"]:
        return False
    if filters["located"] and startup.location_precision not in ("city", "country"):
        return False
    return True


def filter_summary(filters: dict) -> str:
    parts = [
        f"“{filters['keyword']}”" if filters["keyword"] else None,
        ", ".join(filters["countries"]) or None,
        f"founded {filters['since']}+" if filters["since"] else None,
        "with location" if filters["located"] else None,
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


# ---------- data and state ----------

conn = db.connect()
records = db.all_startups_with_keys(conn)
by_key = dict(records)
all_startups = [s for _, s in records]
created = db.created_times(conn)
favorites = storage.favorite_keys(conn)
scouts = storage.saved_searches(conn)
countries_available = sorted({s.country for s in all_startups if s.country})
missing_keys = config.missing_keys()
chat_disabled = "NVIDIA_API_KEY" in missing_keys

for key, value in {"view": "home", "chat_id": None, "pending_question": None, "scout": None, "scout_new": {}}.items():
    st.session_state.setdefault(key, value)
for key, default in EMPTY_FILTERS.items():
    st.session_state.setdefault(f"f_{key}", default)
# Countries that are no longer in the data cannot stay selected in the multiselect
st.session_state.f_countries = [c for c in st.session_state.f_countries if c in countries_available]


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
    st.caption(f"{md_escape(place(startup))} · founded {startup.founding_year or 'unknown'} · {md_escape(startup.industry or 'industry unknown')}")
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
        badge = '<span class="badge-new">New</span>' if is_new else ""
        star = " ⭐" if key in favorites else ""
        st.markdown(f"<b>{escape(startup.name)}</b>{star}{badge}", unsafe_allow_html=True)
        st.caption(f"{md_escape(place(startup))} · {startup.founding_year or 'year unknown'}")
        st.markdown(md_escape(startup.summary or startup.description or ""))
        st.button("Details", key=f"chip_{prefix}_{key}", on_click=show_startup, args=(key,))


# ---------- sidebar ----------

with st.sidebar:
    st.markdown('<div class="brand">🧠 Searchbrain</div>', unsafe_allow_html=True)
    view = st.session_state.view
    st.button("＋  New chat", key="nav_new_chat", on_click=new_chat, type="primary" if view == "home" else "secondary")

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

    st.divider()
    for name, label_text in [("browse", "🔎  Browse startups"), ("charts", "📊  Charts"), ("favorites", "⭐  Favorites")]:
        st.button(label_text, key=f"nav_{name}", on_click=go, args=(name,), type="primary" if view == name else "secondary")

    st.divider()
    with st.expander("➕ Collect a new topic"):
        topic = st.text_input("Topic", placeholder="e.g. humanoid robotics")
        subtopics = st.text_input("Subtopics (comma separated)", placeholder="e.g. robot hand, actuator")
        sub_list = [s.strip() for s in subtopics.split(",") if s.strip()]
        st.caption(f"Uses {3 + len(sub_list)} Tavily searches (free credits) and takes several minutes.")
        if st.button("Start collecting", key="collect_start", disabled=not topic.strip() or bool(missing_keys)):
            with st.status(f"Collecting “{topic.strip()}” …", expanded=True) as status:
                bar = st.progress(0.0, text="Searching the web …")

                def show_progress(number: int, count: int, state: str) -> None:
                    bar.progress(number / count, text=f"AI extraction: batch {number}/{count} ({state})")

                try:
                    report = collect(topic.strip(), sub_list, on_progress=show_progress)
                except Exception as error:
                    logger.exception("Collect from the web page failed")
                    status.update(label=f"Collect failed: {type(error).__name__}", state="error")
                else:
                    failed = report["batches"]["failed"]
                    st.session_state.last_collect = f"“{topic.strip()}”: {report['new']} new, {report['updated']} updated" + (
                        f", {failed} batches failed (start again to retry)" if failed else ""
                    )
                    st.rerun()
        if "last_collect" in st.session_state:
            st.success(f"Last collect – {st.session_state.last_collect}")


# ---------- views ----------


def view_home() -> None:
    st.markdown(
        f'<div class="greeting"><h1>{escape(greeting())}</h1><p>What are we looking for today?</p></div>', unsafe_allow_html=True
    )
    if missing_keys:
        st.warning(f"Missing in .env: {', '.join(missing_keys)}. Chat and collecting need these keys.")
    with st.container(key="home_input"):
        question = st.chat_input("Ask Searchbrain…", key="home_question", disabled=chat_disabled)
    if question:
        start_chat(question)
        st.rerun()

    section_label("💬  Ask Searchbrain anything")
    with st.container(key="suggestions"):
        for column, (index, (icon, title, question_text)) in zip(st.columns(3), enumerate(SUGGESTIONS)):
            column.button(
                f"{icon}  \n**{title}**  \n{question_text}",
                key=f"suggest_{index}",
                on_click=start_chat,
                args=(question_text,),
                disabled=chat_disabled,
            )

    on_map = sum(s.location_precision in ("city", "country") for s in all_startups)
    section_label("🗺️  Where the startups are", f"{len(all_startups)} startups · {len(countries_available)} countries · {on_map} on the map")
    with st.container(border=True):
        show_map(all_startups, key="home_map", height=480)

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


def render_answer(chat_id: int, index: int, message: dict, latest: bool) -> None:
    st.markdown(message["content"])
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

    question = st.session_state.pending_question
    if question:
        with st.container(key=f"user_msg_{len(messages)}"):
            st.markdown(md_escape(question))
        with st.spinner("Searching the database …"):
            history = [{"role": m["role"], "content": m["content"]} for m in messages]
            answer = agent.ask(question, history, conn)
        found = [key for s in answer.startups if (key := db.existing_key(conn, s))]
        messages += [{"role": "user", "content": question}, {"role": "assistant", "content": answer.text, "found": found}]
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
    columns = st.columns([3, 3, 2, 2])
    columns[0].text_input("Search", key="f_keyword", placeholder="Name, description, industry …")
    columns[1].multiselect("Countries", countries_available, key="f_countries", placeholder="All countries")
    columns[2].selectbox("Founded in or after", YEARS, key="f_since", format_func=lambda y: "Any year" if y == 0 else str(y))
    columns[3].checkbox("Only with location", key="f_located")

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
VIEWS.get(st.session_state.view, view_home)()

if detail := st.session_state.pop("detail", None):
    startup_dialog(detail)
