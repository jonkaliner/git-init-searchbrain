# Project: Searchbrain

## Goal
A tool that finds young companies (startups), stores them in a searchable
knowledge base, shows where they are on a world map, and lets me ask
questions about them in a chat.
This is a learning and showcase project, so clarity matters more than features.

## Architecture
Inspired by the n8n "Company Brain" template:
https://n8n.io/workflows/19082-build-a-company-knowledge-brain-with-google-drive-cognee-openai-and-slack/

Four parts, built in this order:
1. Collect: find startups (web search / data sources), extract name,
   description, industry, country, city, founding year and link, and store
   them in a local database.
2. Map: show where the startups are located on a world map (one marker per
   startup, with name, industry and link in a popup).
3. Ask: a chat interface where I ask questions. An AI agent (NVIDIA model)
   uses a search tool over the stored startups and answers with sources.
4. Monitor: log errors to a file.

Unlike the template, the data is not uploaded by people. The tool has to
find it itself, so the collect step decides the quality of everything else.

## Tech
- Language: Python, rebuilt in plain code (not n8n) so I can learn how it works
- Storage: start simple with a local database (e.g. SQLite)
- AI model via API: NVIDIA (build.nvidia.com), OpenAI-compatible endpoint
- Map: Folium (Python library). It generates an interactive map as an HTML
  file that opens in the browser
- Geocoding (city to coordinates): free service such as Nominatim
  (OpenStreetMap). Respect its usage policy: max 1 request per second,
  identify the app in the user agent, and cache results so each city is
  only looked up once
- API keys only in .env, never in code, never in Git (.env is in .gitignore)

## First version (scope)
- Collect startups for one search topic and save them
- Per startup: name, description, industry, country, city, latitude,
  longitude, founding year, link
- Generate a world map (HTML file) with a marker for each stored startup
- Ask questions in a simple chat (terminal is fine) and get answers based
  on the stored data, with the source link
- Log errors to a file
- Streamlit web page with tabs for chat, startup table (filters, detail view,
  CSV download), map, charts and favorites / saved searches

## Later (not now)
- Show the map inside a chat answer, e.g. "show me all battery startups in
  Europe" returns a filtered map
- Slack chat interface and Slack error alerts
- Cognee (knowledge graph), once links between companies matter

## Out of scope for now
- A full web app beyond the local Streamlit page, user accounts
- Anything that needs paid services

## Open questions (ask me before building)
- Where should the startups come from (web search, public databases,
  my own lists)? Respect each site's terms and robots.txt, no scraping of
  sites that forbid it.
- What counts as a young company (e.g. founded in the last 5 years)?
- Which industries, countries and funding stages first?
- What if a source only gives the country, not the city? Proposal: one
  marker per country in that case.

## Rules for Claude
- Communicate with me in English (code, comments and commit messages too)
- First write a plan, only generate code after my approval
- Work in small steps and explain after each step what happened
- Do not delete or overwrite anything without asking
- Never print, log or commit API keys or the contents of .env
- Explain error messages briefly, then fix them
- Suggest a Git commit after every working step
- If a rule here is missing or wrong, propose an update to this file

## How to run
To be filled in once the first version works.

## Decisions made
- 2026-10-29: Python, local database, NVIDIA API, architecture based on the n8n template
- 2026-10-29: world map with Folium and Nominatim geocoding is part of the first version
- 2026-10-29: web search with Tavily (free tier). Gemini with Google Search
  grounding was tried, but its free search quota was not available
- 2026-10-29: NVIDIA model `openai/gpt-oss-20b` (older models were retired);
  can be changed with `NVIDIA_MODEL` in .env
- 2026-10-29: no fixed age limit. Every startup found is stored, founding year
  is a filter when asking
- 2026-10-29: first topic is humanoid robotics (incl. hands, actuators, sensors), worldwide
- 2026-10-29: if only the country is known, show one marker per country
- 2026-10-29: Streamlit web page is part of the first version
