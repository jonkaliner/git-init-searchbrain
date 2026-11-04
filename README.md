# Searchbrain

A tool that finds young companies (startups), stores them in a database, shows where they are on a world map, and
builds **deal flows**: two AI agents search for startups that fit a request and rate every fit from 1 to 5 stars.
Everything runs on GitHub, for free, and updates itself every week.

- **Website** (GitHub Pages): world map, search field, browse table, charts, favorites, saved deal flows ("Scouts").
- **Weekly update** (GitHub Actions): new Y Combinator companies, new posts of 35 accelerators, every Scout searched again.
- **AI answers** in the search field (optional): a small Cloudflare Worker keeps the API key secret, see [worker/README.md](worker/README.md).

This is a learning and showcase project. The code is written to be read, not to be clever.

## How it works

```
every Monday, or when you click "Run workflow" with a request on GitHub
 │
 ├─ Y Combinator list ──► all active YC companies, new ones get a map position (Nominatim, once per city)
 ├─ accelerator feeds ──► new posts of 35 accelerators and incubators (RSS), stored in the database
 └─ for every Scout (a saved request like "AI chip startups for autonomous driving, early stage, Europe or US"):
       Scout agent    searches the database, the accelerator posts and the web with 4 tools,
                      and lists candidates
       Analyst agent  checks the candidates and rates how well each one fits the request (1-5 stars, why, concerns)
       plain code     throws away anything the tools never showed (made-up names, websites, sources),
                      saves startups and matches
 │
 ▼
data/searchbrain.db  ──► docs/data/*.json  ──► website on GitHub Pages
```

The agents only judge. Everything that has to be right is plain code: `crew.py` checks every answer
(`check_candidate`, `build_deal_flow`), so a startup that no tool ever showed cannot end up in the database.

| Part | File |
|---|---|
| The two agents (CrewAI), the checks around them | [searchbrain/crew.py](searchbrain/crew.py) |
| Their tools (LangChain): database, accelerator posts, web search, read a page | [searchbrain/tools.py](searchbrain/tools.py) |
| Web search with DuckDuckGo (Tavily only as an optional backup) | [searchbrain/search.py](searchbrain/search.py) |
| Accelerator RSS feeds: find them once, read them weekly | [searchbrain/feeds.py](searchbrain/feeds.py) |
| Polite reading of pages: robots.txt, no private addresses | [searchbrain/web.py](searchbrain/web.py) |
| Y Combinator list | [searchbrain/yc.py](searchbrain/yc.py) |
| Place name to map position (Nominatim, cached, 1 request per second) | [searchbrain/geocode.py](searchbrain/geocode.py) |
| Saved requests and their matches | [searchbrain/scouts.py](searchbrain/scouts.py) |
| The weekly update, and one deal flow on demand | [searchbrain/update.py](searchbrain/update.py) |
| Database to JSON for the website | [searchbrain/export.py](searchbrain/export.py) |
| The website | [docs/](docs/) |
| The workflows | [.github/workflows/](.github/workflows/) |

The older parts still work on your own computer: `python -m searchbrain.collect` (search and extract without agents),
`python -m searchbrain.agent` (chat in the terminal) and the Folium map `data/map.html`.

## Put it on GitHub (once)

1. **Make the repository public.** GitHub Pages is free only for public repositories. Everything in it is public then:
   the code, the database and the website data. Your keys are not in it (see below).
2. **Add the secret.** Repository, *Settings*, *Secrets and variables*, *Actions*, *New repository secret*:
   `NVIDIA_API_KEY` (key from <https://build.nvidia.com>). Optional: `TAVILY_API_KEY`, a backup for the web search.
3. **Switch on Pages.** *Settings*, *Pages*, *Build and deployment*, Source: **GitHub Actions**.
4. **Run it once.** *Actions* tab, *Update*, *Run workflow*. The first full run takes a while.
   The site is then at `https://<your-name>.github.io/<repository>/`.
5. **Optional: AI answers** in the search field. Set up the Worker in [worker/](worker/README.md) and put its address
   into `docs/config.js`.

Build a deal flow: *Actions*, *Update*, *Run workflow*, type a request like
`Build a deal flow for autonomous driving AI chip startups, early stage, Europe or US`. It is saved as a Scout and
searched again every Monday. Delete a Scout with the same form (field "remove", the id is in `docs/data/scouts.json`).

## Run it on your own computer

You need **Python 3.13** (CrewAI does not support newer versions yet). On Windows: `py install 3.13`.

```
py -3.13 -m venv .venv313
.venv313\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          then fill in NVIDIA_API_KEY and NOMINATIM_USER_AGENT
python check_apis.py            tests the three services
```

| Command | What it does |
|---|---|
| `python -m searchbrain.update` | the weekly update |
| `python -m searchbrain.update --request "Build a deal flow for ..."` | one deal flow, saved as a Scout |
| `python -m searchbrain.crew "Build a deal flow for ..."` | the two agents alone, prints the matches |
| `python -m searchbrain.feeds discover` / `refresh` / `search "robot hand"` | the accelerator feeds |
| `python -m searchbrain.yc import` | all active Y Combinator companies |
| `python -m pytest` | the tests (about 100, no real API calls) |
| `python -m http.server 8000` then open `http://localhost:8000/docs/` | the website |

Browser tests for the website code: open `http://localhost:8000/tests/web/lib.test.html` and `worker.test.html`
(started the same way). They must say `ALL PASSED`.

## Honest limits

- **DuckDuckGo has no official search API.** The library asks its web page, and after a handful of quick requests
  DuckDuckGo answers with nothing. The search is paced, rests after refusals, and the agents carry on with the database
  and the accelerator posts. With a `TAVILY_API_KEY` the web search has a backup. Searches from GitHub's servers are
  refused more often than from a home computer.
- **35 of the 100 accelerators publish an RSS feed** (`sources/feeds.csv`). The others are only reached through the web search.
- **Y Combinator publishes no founding year**, so the "founded" filter only knows other startups. The batch (W24, S25, ...)
  is shown instead.
- **The stars are an AI's judgment** from what the tools showed. Read the "why it fits" and "concern" lines.
- **The free NVIDIA tier is slow and sometimes busy.** A deal flow takes 4 to 8 minutes.
- **A startup's location** comes from its city and country and is one point per city. Remote companies have none.
- Nominatim (OpenStreetMap) may be used for 1 request per second; every place is looked up only once.

## Keys and privacy

Keys live in `.env` on your computer and in GitHub *secrets*, never in the code. `.env` is in `.gitignore`.
The website and the database only contain public company data. Favorites and chats on the website are saved in your
own browser only.
