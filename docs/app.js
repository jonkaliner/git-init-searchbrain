// Searchbrain website: reads the JSON files in data/ and shows the map, the Scouts, a table and charts.
// Everything that comes from the data is put on the page with textContent (never innerHTML), because
// the descriptions come from the web.
import * as lib from "./lib.js";

const CONFIG = window.SEARCHBRAIN_CONFIG || {};
const MAIN = document.getElementById("main");
const DIALOG = document.getElementById("dialog");
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
const PAGE_SIZE = 150;
const MAX_CHATS = 15;

// ---------- small helpers ----------

/** Create an element. Strings in `children` become text, never HTML. */
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "href") {
      const internal = String(value).startsWith("#/");
      const safe = internal ? value : lib.safeUrl(value);
      if (!safe) continue;
      el.setAttribute("href", safe);
      if (!internal) {
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
      }
    } else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Replace the content of an element. Unlike replaceChildren, null and false are skipped (not written as the text "null"). */
function fill(parent, ...children) {
  parent.replaceChildren(...children.flat(Infinity).filter((child) => child != null && child !== false));
}

const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`sb.${key}`);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`sb.${key}`, JSON.stringify(value));
    } catch {
      /* private window or blocked storage: the page works, it just does not remember */
    }
  },
};

const state = {
  startups: [],
  byKey: new Map(),
  scouts: [],
  meta: {},
  favorites: new Set(store.get("favorites", [])),
  chats: store.get("chats", []),
  chat: null, // the chat shown on the start page
  filters: { keyword: "", country: "", since: 0, located: false, yc: false },
  sort: { key: "name", dir: 1 },
  shown: PAGE_SIZE,
  map: null,
};

const today = () => new Date().toISOString().slice(0, 10);
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString("en")} ${n === 1 ? one : many}`;

function saveFavorites() {
  store.set("favorites", [...state.favorites]);
}

function toggleFavorite(key) {
  state.favorites.has(key) ? state.favorites.delete(key) : state.favorites.add(key);
  saveFavorites();
  renderSidebar();
}

function visits() {
  return store.get("visits", {});
}

function newMatchesOf(scout) {
  return lib.newMatches(scout, visits()[scout.id]);
}

// ---------- map ----------

function popupNode(s) {
  const details = [s.industry, s.city, s.country, s.year, s.batch && `YC ${s.batch}`].filter(Boolean).join(" · ");
  return h("div", {}, h("strong", { text: s.name }), h("p", { class: "muted", text: details }), h("p", { text: s.summary || s.description || "" }),
    h("p", {}, h("a", { href: lib.websiteLink(s).url, text: lib.websiteLink(s).text }), " ", h("a", { href: s.source, text: "Source" }), " ", h("a", { href: "#/", text: "Details", onclick: (e) => { e.preventDefault(); openStartup(s.key); } })));
}

/** A Leaflet map in `element`. update(list) replaces the markers. Returns null when the map library did not load. */
function createMap(element) {
  if (!window.L || !L.markerClusterGroup) {
    element.append(h("p", { class: "muted", text: "The map could not be loaded (the map library is blocked or offline)." }));
    return null;
  }
  const map = L.map(element, { worldCopyJump: true, minZoom: 2, scrollWheelZoom: true }).setView([25, 10], 2);
  const attribution = "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ";
  // a light grey base map and a separate layer with English place names
  L.tileLayer(`${ESRI}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`, { attribution, maxZoom: 16 }).addTo(map);
  L.tileLayer(`${ESRI}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16 }).addTo(map);
  const cluster = L.markerClusterGroup({ chunkedLoading: true, showCoverageOnHover: false, maxClusterRadius: 45 }).addTo(map);
  const countries = L.layerGroup().addTo(map);

  function update(list, { fit = false } = {}) {
    cluster.clearLayers();
    countries.clearLayers();
    const markers = [];
    const byCountry = new Map();
    for (const s of list) {
      if (s.lat == null || s.lon == null) continue;
      if (s.precision === "city") {
        const marker = L.circleMarker([s.lat, s.lon], { radius: 6, color: "#1d4ed8", weight: 1, fillColor: "#2563eb", fillOpacity: 0.85 });
        marker.bindTooltip(s.name);
        marker.bindPopup(() => popupNode(s), { maxWidth: 320 });
        markers.push(marker);
      } else if (s.precision === "country" && s.country) {
        if (!byCountry.has(s.country)) byCountry.set(s.country, []);
        byCountry.get(s.country).push(s);
      }
    }
    cluster.addLayers(markers);
    // only the country is known: one marker per country
    for (const [country, group] of byCountry) {
      const marker = L.circleMarker([group[0].lat, group[0].lon], { radius: 9, color: "#c2410c", weight: 2, fillColor: "#f97316", fillOpacity: 0.85 });
      marker.bindTooltip(`${country}: ${plural(group.length, "startup")}, city unknown`);
      marker.bindPopup(() => h("div", {}, h("strong", { text: `${country}: ${plural(group.length, "startup")}, city unknown` }), h("ul", {}, group.map((s) => h("li", {}, popupNode(s))))), { maxWidth: 360 });
      marker.addTo(countries);
    }
    if (fit && (markers.length || byCountry.size)) {
      const points = [...list.filter((s) => s.lat != null).map((s) => [s.lat, s.lon])];
      map.fitBounds(L.latLngBounds(points), { maxZoom: 6, padding: [30, 30] });
    }
  }
  return { update, destroy: () => map.remove() };
}

function mountMap(id, list, options) {
  const element = document.getElementById(id);
  state.map = createMap(element);
  state.map?.update(list, options);
}

// ---------- pieces of the page ----------

function programsLine(s) {
  const names = (s.programs || "").split(";").map((n) => n.trim()).filter(Boolean);
  const parts = [];
  if (names.includes("Y Combinator")) parts.push(h("span", {}, h("span", { class: "label", text: "Backed by: " }), "Y Combinator"));
  const others = names.filter((n) => n !== "Y Combinator");
  if (others.length) parts.push(h("span", {}, h("span", { class: "label", text: "Found via: " }), `${others.join(", ")} website`));
  return parts.length ? h("p", {}, parts.flatMap((p, i) => (i ? [" · ", p] : [p]))) : null;
}

function field(label, value) {
  return h("span", {}, h("span", { class: "label", text: `${label}: ` }), value);
}

/** One profile, like the "Detailed Profiles" of a Scout. `match` adds stars, the reason and concerns. */
function profile(s, number, match) {
  const facts = [field("Location", lib.placeOf(s)), field("Founded", s.year || "unknown")];
  if (s.batch) facts.push(field("YC", s.batch));
  if (s.industry) facts.push(field("Industry", s.industry));
  const site = lib.websiteLink(s);
  const links = [h("a", { href: site.url, text: site.text }), s.source && h("a", { href: s.source, text: "Source" })].filter(Boolean);
  return h("div", { class: "profile" },
    h("div", { class: "profile-head" },
      h("span", { class: "number", text: `${number}.` }),
      h("button", { class: "chip", text: s.name, onclick: () => openStartup(s.key) }),
      match?.score ? h("span", { class: "fit" }, "— Fit: ", `${match.score}/5 `, h("span", { class: "stars", text: lib.stars(match.score) })) : null,
      match && match.isNew ? h("span", { class: "badge new", text: "New" }) : null),
    h("p", {}, facts.flatMap((f, i) => (i ? [" · ", f] : [f]))),
    programsLine(s),
    h("p", {}, field("What they do", s.summary || s.description || "no description yet")),
    match?.why ? h("p", {}, field("Why it fits", match.why)) : null,
    match?.concerns ? h("p", {}, field("Concern", match.concerns)) : null,
    links.length ? h("p", { class: "links" }, links) : null);
}

function openStartup(key) {
  const s = state.byKey.get(key);
  if (!s) return;
  const favorite = h("button", { class: "btn", onclick: () => { toggleFavorite(key); favorite.textContent = state.favorites.has(key) ? "★ Remove favorite" : "☆ Add to favorites"; if (location.hash === "#/favorites") render(); } });
  favorite.textContent = state.favorites.has(key) ? "★ Remove favorite" : "☆ Add to favorites";
  const caption = [lib.placeOf(s), `founded ${s.year || "unknown"}`, s.batch && `Y Combinator ${s.batch}`, s.industry || "industry unknown"].filter(Boolean).join(" · ");
  fill(DIALOG, 
    h("h3", { text: s.name }),
    h("p", { class: "muted", text: caption }),
    programsLine(s),
    h("p", { text: s.summary || s.description || "No description yet." }),
    s.description && s.summary && s.description !== s.summary ? h("details", {}, h("summary", { text: "Original description from the source" }), h("p", { text: s.description })) : null,
    h("div", { class: "btn-row" },
      h("a", { class: "btn", href: lib.websiteLink(s).url, text: `🌐 ${lib.websiteLink(s).text}` }),
      s.source ? h("a", { class: "btn", href: s.source, text: "📰 Source" }) : null,
      favorite,
      h("button", { class: "btn", text: "Close", onclick: () => DIALOG.close() })));
  if (!DIALOG.open) DIALOG.showModal();
}
DIALOG.addEventListener("click", (event) => { if (event.target === DIALOG) DIALOG.close(); });

// ---------- views ----------

function viewHome() {
  const name = (CONFIG.userName || "").trim();
  const placeholder = window.innerWidth < 600 ? "Ask Searchbrain…" : "Ask Searchbrain… e.g. “Build a deal flow for AI chip startups in Europe”";
  const ask = h("textarea", { rows: "1", placeholder, maxlength: "500", "aria-label": "Ask Searchbrain" });
  const send = h("button", { type: "submit", text: "↑", "aria-label": "Send", disabled: true });
  ask.addEventListener("input", () => { send.disabled = !ask.value.trim(); ask.style.height = "auto"; ask.style.height = `${ask.scrollHeight}px`; });
  ask.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); form.requestSubmit(); } });
  const form = h("form", { class: "ask", onsubmit: (event) => { event.preventDefault(); const q = ask.value; ask.value = ""; send.disabled = true; ask.style.height = "auto"; askQuestion(q); } }, ask, send);

  const located = state.startups.filter((s) => s.lat != null).length;
  const countries = new Set(state.startups.map((s) => s.country).filter(Boolean)).size;
  const scouts = state.scouts.slice(0, 3).map((scout) => {
    const fresh = newMatchesOf(scout).length;
    return h("div", { class: "card" }, h("strong", { text: lib.scoutTitle(scout.request, 70) }),
      h("p", { class: "muted", text: `${plural(scout.matches.length, "match", "matches")} · last run ${scout.lastRun || "never"}` }),
      h("div", { class: "btn-row" }, h("a", { class: "btn", href: `#/scout/${scout.id}`, text: fresh ? `${fresh} new · Open` : "Open" })));
  });
  scouts.push(h("div", { class: "card" }, h("strong", { text: "＋ New Scout" }),
    h("p", { class: "muted", text: "Ask for a deal flow above. Agents on GitHub build it and search again every week." })));

  fill(MAIN, 
    h("div", { class: "greeting" }, h("h1", { text: lib.greeting(new Date(), name) }), h("p", { text: "What are we looking for today?" })),
    form,
    h("p", { class: "hint", text: CONFIG.workerUrl ? "Enter to send · answers are written by an AI from the saved startups" : "Enter to send · searches the saved startups" }),
    h("div", { class: "thread", id: "thread" }),
    h("div", { class: "section-label" }, h("span", { text: "🗺️  Where the startups are" }),
      h("span", { class: "muted", id: "map-note", text: "Search above to see the found startups here" })),
    h("div", { class: "card map-card" }, h("div", { id: "home-map" })),
    h("div", { class: "section-label" }, h("span", { text: "◉  Your Scouts" }), h("span", { text: "Saved deal flows that show what is new" })),
    h("div", { class: "cards" }, scouts));
  mountMap("home-map", []); // empty until something is searched
  renderThread();
}

function viewScout(id) {
  const scout = state.scouts.find((s) => s.id === id);
  if (!scout) return location.replace("#/");
  const fresh = new Set(newMatchesOf(scout).map((m) => m.key));
  const rows = scout.matches.map((m) => ({ match: { ...m, isNew: fresh.has(m.key) }, s: state.byKey.get(m.key) })).filter((row) => row.s);
  store.set("visits", { ...visits(), [scout.id]: today() }); // the next visit counts from today

  fill(MAIN, 
    h("div", { class: "section-label" }, h("a", { href: "#/", text: "← Home" }), h("span", { class: "muted", text: `Last run ${scout.lastRun || "never"}` })),
    h("h2", { text: lib.scoutTitle(scout.request, 120) }),
    h("p", { class: "muted", text: scout.request }),
    h("p", { class: "muted", text: `${plural(rows.length, "match", "matches")} · ${fresh.size} new since your last visit` }),
    rows.length
      ? h("div", { class: "card profiles" }, h("h4", { text: `Potential matches (${rows.length})` }), rows.map((row, i) => profile(row.s, i + 1, row.match)))
      : h("div", { class: "card" }, h("p", { text: "This Scout has no matches yet. The agents search again with the next weekly update." })),
    h("div", { class: "section-label" }, h("span", { text: "🗺️  Where the matches are" })),
    h("div", { class: "card map-card" }, h("div", { id: "scout-map" })));
  mountMap("scout-map", rows.map((row) => row.s), { fit: true });
  renderSidebar();
}

function filteredStartups() {
  const f = { ...state.filters, countries: state.filters.country ? [state.filters.country] : [] };
  const { key, dir } = state.sort;
  const found = state.startups.filter((s) => lib.matchesFilters(s, f));
  const value = (s) => (key === "year" ? s.year ?? -1 : String(s[key] ?? "").toLowerCase());
  const empty = (s) => s[key] == null || s[key] === "";
  // empty cells always go to the bottom, in both sort directions
  return found.sort((a, b) => (empty(a) - empty(b)) || (empty(a) ? 0 : value(a) < value(b) ? -dir : value(a) > value(b) ? dir : 0) || a.name.localeCompare(b.name));
}

const COLUMNS = [
  { label: "★", cell: (s) => (state.favorites.has(s.key) ? "⭐" : "") },
  { label: "Name", key: "name", cell: (s) => s.name, cls: "name" },
  { label: "Industry", key: "industry", cell: (s) => s.industry },
  { label: "City", key: "city", cell: (s) => s.city },
  { label: "Country", key: "country", cell: (s) => s.country },
  { label: "Founded", key: "year", cell: (s) => s.year },
  { label: "YC", key: "batch", cell: (s) => s.batch },
  { label: "Program", key: "programs", cell: (s) => s.programs },
  { label: "Website", cell: (s) => h("a", { href: lib.websiteLink(s).url, text: lib.websiteLink(s).text, onclick: (e) => e.stopPropagation() }) },
  { label: "Source", cell: (s) => (s.source ? h("a", { href: s.source, text: "Source", onclick: (e) => e.stopPropagation() }) : "") },
];

const CSV_COLUMNS = [
  { label: "Name", value: (s) => s.name }, { label: "Description", value: (s) => s.description }, { label: "Industry", value: (s) => s.industry },
  { label: "City", value: (s) => s.city }, { label: "Country", value: (s) => s.country }, { label: "Founded", value: (s) => s.year },
  { label: "YC batch", value: (s) => s.batch }, { label: "Programs", value: (s) => s.programs }, { label: "Website", value: (s) => s.link },
  { label: "Source", value: (s) => s.source }, { label: "Latitude", value: (s) => s.lat }, { label: "Longitude", value: (s) => s.lon },
];

function downloadCsv(list) {
  const blob = new Blob([lib.toCsv(list, CSV_COLUMNS)], { type: "text/csv;charset=utf-8" });
  const link = h("a", { href: "#/browse" });
  link.href = URL.createObjectURL(blob);
  link.download = "searchbrain_startups.csv";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function viewBrowse() {
  const f = state.filters;
  const countryCounts = lib.countBy(state.startups, (s) => s.country || null, 500).filter(([c]) => c !== "unknown");
  const years = Array.from({ length: new Date().getFullYear() - 1999 }, (_, i) => new Date().getFullYear() - i);
  let timer;
  const keyword = h("input", { type: "search", placeholder: "Name, description, industry …", value: f.keyword, "aria-label": "Search" });
  keyword.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { f.keyword = keyword.value; state.shown = PAGE_SIZE; renderResults(); }, 200); });
  const country = h("select", { "aria-label": "Country", onchange: () => { f.country = country.value; state.shown = PAGE_SIZE; renderResults(); } },
    h("option", { value: "", text: "All countries" }), countryCounts.map(([c, n]) => h("option", { value: c, text: `${c} (${n})`, selected: c === f.country })));
  const since = h("select", { "aria-label": "Founded in or after", onchange: () => { f.since = Number(since.value); state.shown = PAGE_SIZE; renderResults(); } },
    h("option", { value: "0", text: "Any year" }), years.map((y) => h("option", { value: String(y), text: String(y), selected: y === f.since })));
  const located = h("input", { type: "checkbox", checked: f.located, onchange: () => { f.located = located.checked; renderResults(); } });
  const yc = h("input", { type: "checkbox", checked: f.yc, onchange: () => { f.yc = yc.checked; renderResults(); } });
  located.checked = f.located;
  yc.checked = f.yc;

  fill(MAIN, 
    h("h2", { text: "Browse startups" }),
    h("div", { class: "card", style: "margin-top:1rem" },
      h("div", { class: "filters" },
        h("label", {}, "Search", keyword), h("label", {}, "Country", country), h("label", {}, "Founded in or after", since),
        h("div", { class: "checks" }, h("label", {}, located, " Only with location"), h("label", {}, yc, " Only YC companies")))),
    h("div", { id: "results" }),
    h("div", { class: "section-label" }, h("span", { text: "🗺️  On the map" })),
    h("div", { class: "card map-card" }, h("div", { id: "browse-map" })));
  mountMap("browse-map", []);
  renderResults();

  function renderResults() {
    const found = filteredStartups();
    const visible = found.slice(0, state.shown);
    const head = h("tr", {}, COLUMNS.map((c) => h("th", {
      text: c.label + (c.key && state.sort.key === c.key ? (state.sort.dir > 0 ? " ▲" : " ▼") : ""),
      onclick: c.key ? () => { state.sort = { key: c.key, dir: state.sort.key === c.key ? -state.sort.dir : 1 }; renderResults(); } : null,
    })));
    const body = visible.map((s) => h("tr", { onclick: () => openStartup(s.key), tabindex: "0", onkeydown: (e) => { if (e.key === "Enter") openStartup(s.key); } },
      COLUMNS.map((c) => h("td", { class: c.cls }, c.cell(s)))));
    fill(document.getElementById("results"), 
      h("div", { class: "toolbar" },
        h("span", { class: "muted", text: `${found.length.toLocaleString("en")} of ${state.startups.length.toLocaleString("en")} startups` }),
        h("span", { class: "spacer" }),
        h("button", { class: "btn", text: "Reset filters", onclick: () => { state.filters = { keyword: "", country: "", since: 0, located: false, yc: false }; state.shown = PAGE_SIZE; render(); } }),
        h("button", { class: "btn", text: "⬇️ CSV", onclick: () => downloadCsv(found) })),
      found.length
        ? h("div", { class: "card table-wrap", style: "padding:0" }, h("table", {}, h("thead", {}, head), h("tbody", {}, body)))
        : h("div", { class: "card", text: "No startups match the filters." }),
      found.length > visible.length
        ? h("div", { class: "more" }, h("button", { class: "btn", text: `Show ${Math.min(PAGE_SIZE, found.length - visible.length)} more`, onclick: () => { state.shown += PAGE_SIZE; renderResults(); } }))
        : null);
    state.map?.update(found, { fit: found.length > 0 && found.length < state.startups.length });
  }
}

function barChart(entries, grey) {
  const max = Math.max(1, ...entries.map(([, n]) => n));
  return entries.map(([name, n]) => h("div", { class: "chart-row" },
    h("span", { class: "name", title: String(name), text: String(name) }),
    h("div", {}, h("div", { class: `bar${grey ? " grey" : ""}`, style: `width:${(n / max) * 100}%` })),
    h("span", { class: "count", text: n.toLocaleString("en") })));
}

/** Columns for a series that is already in the order it should be shown. */
function columnChart(entries, labelOf, note) {
  const max = Math.max(1, ...entries.map(([, n]) => n));
  const step = Math.ceil(entries.length / 24); // with many columns only every second or third one gets a label
  return h("div", {},
    h("div", { class: "columns" }, entries.map(([key, n], index) => h("div", { class: "col", title: `${key}: ${n}` },
      h("div", { class: "bar", style: `height:${(n / max) * 100}%` }), h("span", { class: "year", text: index % step === 0 ? labelOf(key) : "" })))),
    note ? h("p", { class: "muted", text: note }) : null);
}

/** "W24" (winter 2024) -> a number to sort batches in time order: winter, spring (X), summer, fall. */
function batchOrder(code) {
  return Number(code.slice(1)) * 4 + "WXSF".indexOf(code[0]);
}

function viewCharts() {
  const batches = lib.countBy(state.startups.filter((s) => s.batch), (s) => s.batch, 200).sort((a, b) => batchOrder(a[0]) - batchOrder(b[0]));
  fill(MAIN,
    h("h2", { text: "Charts" }),
    h("div", { class: "charts", style: "margin-top:1rem" },
      h("div", { class: "card" }, h("strong", { text: "By country" }), barChart(lib.countBy(state.startups, (s) => s.country || "unknown"))),
      h("div", { class: "card" }, h("strong", { text: "By industry" }), barChart(lib.countBy(state.startups, (s) => s.industry || "unknown"), true))),
    h("div", { class: "card" }, h("strong", { text: "By Y Combinator batch" }),
      batches.length
        ? columnChart(batches, (code) => code, "W = winter, X = spring, S = summer, F = fall. The number is the year, e.g. W24 = winter 2024.")
        : h("p", { class: "muted", text: "No Y Combinator companies yet." })));
}

function viewFavorites() {
  const list = [...state.favorites].map((key) => state.byKey.get(key)).filter(Boolean);
  fill(MAIN, 
    h("h2", { text: "⭐ Favorites" }),
    list.length
      ? h("div", { class: "card profiles", style: "margin-top:1rem" }, list.map((s, i) => profile(s, i + 1)))
      : h("p", { class: "muted", text: "No favorites yet. Open a startup's details and click “Add to favorites”. They are saved in this browser only." }));
}

// ---------- ask ----------

function ensureChat(firstQuestion) {
  if (!state.chat) {
    state.chat = { id: Date.now().toString(36), title: firstQuestion.slice(0, 48), messages: [] };
    state.chats.unshift(state.chat);
    state.chats = state.chats.slice(0, MAX_CHATS);
  }
  return state.chat;
}

function saveChats() {
  store.set("chats", state.chats);
  renderSidebar();
}

function localAnswer(results, dealFlow) {
  if (!results.length) {
    return dealFlow
      ? "I found no startup in the saved data for this yet. A deal flow lets the agents search the accelerator news and the web for it."
      : "I found no startup in the saved data that matches. Try other words, or ask for a deal flow in this area.";
  }
  const best = results.slice(0, 3).map((s) => s.name);
  const list = best.length > 1 ? `${best.slice(0, -1).join(", ")} and ${best.at(-1)}` : best[0];
  return `I found ${results.length >= 12 ? "at least 12" : results.length} ${results.length === 1 ? "startup" : "startups"} in the saved data that match. The closest are ${list}${list.endsWith(".") ? "" : "."}`;
}

async function streamAnswer(question, history, results, onText) {
  const response = await fetch(CONFIG.workerUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history, startups: results.map(lib.workerRecord) }),
  });
  if (!response.ok || !response.body) throw new Error(`Worker answered ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let text = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data:") || line.includes("[DONE]")) continue;
      try {
        text += JSON.parse(line.slice(5)).choices?.[0]?.delta?.content || "";
        onText(text);
      } catch {
        /* a half line or a keep-alive: ignore */
      }
    }
  }
  return text.trim();
}

async function askQuestion(raw) {
  const question = raw.trim().slice(0, 500);
  if (!question) return;
  const chat = ensureChat(question);
  const history = chat.messages.slice(-6).map((m) => ({ role: m.role, content: m.text.slice(0, 500) }));
  const results = lib.searchStartups(state.startups, question, { limit: 12 });
  const dealFlow = lib.isDealFlowRequest(question);
  const answer = { role: "assistant", text: localAnswer(results, dealFlow), keys: results.map((s) => s.key), dealFlow, request: question };
  chat.messages.push({ role: "user", text: question }, answer);
  saveChats();
  renderThread();

  if (CONFIG.workerUrl && results.length) {
    const paragraph = document.querySelector("#thread .answer:last-child p.text");
    try {
      const text = await streamAnswer(question, history, results, (partial) => { if (paragraph) paragraph.textContent = partial; });
      if (text) answer.text = text;
    } catch {
      answer.text += " (The AI answer is not available right now.)";
    }
    saveChats();
    renderThread();
  }
}

/** A small form for a new Scout. The agents run on GitHub, so the form copies the request and opens the workflow page. */
function openNewScout() {
  const input = h("textarea", { rows: "3", maxlength: "500", placeholder: "Build a deal flow for … e.g. lidar startups in Europe, early stage", "aria-label": "Request for the new Scout", style: "width:100%;border:1px solid var(--line);border-radius:10px;padding:.5rem .7rem;resize:vertical" });
  const copy = h("button", { class: "btn primary", text: "Copy request and open GitHub" });
  copy.addEventListener("click", async () => {
    const request = input.value.trim().replace(/^(?!build a deal flow)/i, "Build a deal flow for ");
    if (!input.value.trim()) return input.focus();
    try { await navigator.clipboard.writeText(request); copy.textContent = "Copied ✓ opening GitHub …"; } catch { copy.textContent = "Select the text and copy it yourself"; }
    if (CONFIG.repo) window.open(`https://github.com/${CONFIG.repo}/actions/workflows/update.yml`, "_blank", "noopener");
  });
  fill(DIALOG,
    h("h3", { text: "◉ New Scout" }),
    h("p", { class: "muted", text: "A Scout is a deal flow request. Two AI agents search Y Combinator, the accelerators and the web, rate every fit from 1 to 5 stars, and search again every Monday." }),
    input,
    h("ol", { class: "muted" },
      h("li", { text: "Describe the topic, the stage and the region." }),
      h("li", { text: "Click the button: the request is copied and the GitHub page opens." }),
      h("li", { text: "There click “Run workflow”, paste the request into the first field and start it." }),
      h("li", { text: "After about 10 minutes reload this site: the Scout is in the sidebar." })),
    h("div", { class: "btn-row" }, copy, h("button", { class: "btn", text: "Close", onclick: () => DIALOG.close() })));
  if (!DIALOG.open) DIALOG.showModal();
  input.focus();
}

function dealFlowCard(request) {
  const repo = CONFIG.repo;
  const existing = state.scouts.find((s) => s.request.toLowerCase().replace(/\s+/g, " ") === request.toLowerCase().replace(/\s+/g, " "));
  const copy = h("button", { class: "btn", text: "Copy request" });
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(request); copy.textContent = "Copied ✓"; } catch { copy.textContent = "Select the text above and copy it"; }
  });
  return h("div", { class: "card" },
    h("strong", { text: "🔍 Build a deal flow for this area?" }),
    h("p", { class: "muted", text: "Two AI agents do this on GitHub: the Scout searches Y Combinator, the accelerator news and the web, the Analyst rates every fit from 1 to 5 stars. Saved Scouts are searched again every Monday." }),
    h("p", {}, h("em", { text: request })),
    h("p", { class: "muted", text: "1. Copy the request. 2. Open the workflow, click “Run workflow” and paste the request. The result appears here after a few minutes." }),
    h("div", { class: "btn-row" }, copy,
      repo ? h("a", { class: "btn primary", href: `https://github.com/${repo}/actions/workflows/update.yml`, text: "Open the workflow on GitHub" }) : null,
      existing ? h("a", { class: "btn", href: `#/scout/${existing.id}`, text: "Open the existing Scout" }) : null));
}

function renderThread() {
  const thread = document.getElementById("thread");
  if (!thread) return;
  const messages = state.chat?.messages || [];
  fill(thread, ...messages.map((m) => {
    if (m.role === "user") return h("div", { class: "bubble-user", text: m.text });
    const found = (m.keys || []).map((k) => state.byKey.get(k)).filter(Boolean);
    return h("div", { class: "answer" },
      h("p", { class: "text", text: m.text }),
      m.dealFlow ? dealFlowCard(m.request) : null,
      found.length ? h("div", { class: "card profiles" }, h("h4", { text: `Potential matches (${found.length})` }), found.map((s, i) => profile(s, i + 1)),
        h("p", { class: "muted", text: "Found in the saved data, ranked by your words. Stars come from a deal flow, where the Analyst rates every fit." })) : null);
  }));
  thread.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  showResultsOnMap(messages);
}

/** The home map shows the startups of the latest answer, and nothing before the first search. */
function showResultsOnMap(messages) {
  const latest = [...messages].reverse().find((m) => m.role === "assistant");
  const found = (latest?.keys || []).map((k) => state.byKey.get(k)).filter(Boolean);
  state.map?.update(found, { fit: true });
  const note = document.getElementById("map-note");
  if (note) {
    const placed = found.filter((s) => s.lat != null).length;
    note.textContent = latest ? `${plural(found.length, "startup")} found · ${placed} with a location` : "Search above to see the found startups here";
  }
}

// ---------- sidebar and routing ----------

function route() {
  const [name = "", arg = ""] = location.hash.replace(/^#\/?/, "").split("/");
  return { name, arg };
}

function navItem(label, href, active, extra) {
  return h("a", { class: `nav-item${active ? " active" : ""}`, href, onclick: () => document.getElementById("sidebar").classList.remove("open") },
    h("span", { class: "label", text: label }), extra || null);
}

function renderSidebar() {
  const { name, arg } = route();
  // Home and New chat both start with an empty start page
  const fresh = () => { state.chat = null; document.getElementById("sidebar").classList.remove("open"); ["", "#/"].includes(location.hash) ? render() : (location.hash = "#/"); };
  fill(document.getElementById("nav"), 
    h("button", { class: `nav-item${name === "" ? " active" : ""}`, onclick: fresh }, h("span", { class: "label", text: "🏠  Home" })),
    navItem("🔎  Browse startups", "#/browse", name === "browse"),
    navItem("📊  Charts", "#/charts", name === "charts"),
    navItem(`⭐  Favorites${state.favorites.size ? ` (${state.favorites.size})` : ""}`, "#/favorites", name === "favorites"),
    h("button", { class: "nav-item", onclick: fresh }, h("span", { class: "label", text: "＋  New chat" })),
    h("button", { class: "nav-item", onclick: () => { document.getElementById("sidebar").classList.remove("open"); openNewScout(); } }, h("span", { class: "label", text: "◉  New Scout" })));
  fill(document.getElementById("scout-nav"), ...(state.scouts.length
    ? state.scouts.map((scout) => {
        const fresh = newMatchesOf(scout).length;
        const active = name === "scout" && arg === scout.id;
        return navItem(`◉  ${lib.scoutTitle(scout.request, 28)}`, `#/scout/${scout.id}`, active, fresh && !active ? h("span", { class: "new", text: `${fresh} new` }) : null);
      })
    : [h("span", { class: "nav-item faint", text: "No Scouts yet" })]));
  fill(document.getElementById("chat-nav"), ...(state.chats.length
    ? [...state.chats.map((chat) => navItem(chat.title, `#/chat/${chat.id}`, name === "chat" && arg === chat.id)),
       h("button", { class: "nav-item faint", text: "Clear chats", onclick: () => { state.chats = []; state.chat = null; store.set("chats", []); render(); } })]
    : [h("span", { class: "nav-item faint", text: "No chats yet" })]));
  const when = (state.meta.generatedAt || "").slice(0, 10);
  fill(document.getElementById("side-foot"), 
    when ? h("div", { text: `Data from ${when}` }) : null,
    CONFIG.repo ? h("a", { href: `https://github.com/${CONFIG.repo}`, text: "Source code on GitHub" }) : null);
}

function render() {
  state.map?.destroy();
  state.map = null;
  const { name, arg } = route();
  if (name === "chat") {
    state.chat = state.chats.find((c) => c.id === arg) || null;
    if (!state.chat) return location.replace("#/");
  }
  if (name === "browse") viewBrowse();
  else if (name === "charts") viewCharts();
  else if (name === "favorites") viewFavorites();
  else if (name === "scout") viewScout(arg);
  else viewHome();
  renderSidebar();
}

async function load() {
  const get = async (file) => {
    const response = await fetch(`data/${file}`, { cache: "no-cache" });
    if (!response.ok) throw new Error(`${file}: ${response.status}`);
    return response.json();
  };
  [state.startups, state.scouts, state.meta] = await Promise.all([get("startups.json"), get("scouts.json"), get("meta.json")]);
  state.byKey = new Map(state.startups.map((s) => [s.key, s]));
  state.scouts.forEach((scout) => scout.matches.sort((a, b) => b.score - a.score));
}

document.getElementById("menu").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
window.addEventListener("hashchange", render);

load().then(render).catch((error) => {
  fill(MAIN, h("div", { class: "card" }, h("strong", { text: "No data yet" }),
    h("p", { class: "muted", text: `The data files could not be loaded (${error.message}). Run the “Update” workflow on GitHub once, it creates them.` })));
});
