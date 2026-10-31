"""World map of the stored startups with Folium.

- One marker per startup whose city is known (grouped when they are close together).
- One marker per country for startups where only the country is known.
"""

from collections import defaultdict
from html import escape
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

from searchbrain.models import Startup

MAP_FILE = Path("data") / "map.html"


def safe_url(url: str | None) -> str | None:
    """Only allow normal web links in popups."""
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if "." in url and " " not in url:
        return f"https://{url}"
    return None


def _link(url: str | None, label: str) -> str:
    safe = safe_url(url)
    return f'<a href="{escape(safe)}" target="_blank">{escape(label)}</a>' if safe else ""


def _popup_html(startup: Startup) -> str:
    details = " · ".join(
        escape(str(part))
        for part in [startup.industry, startup.city, startup.country, startup.founding_year, startup.yc_batch and f"YC {startup.yc_batch}"]
        if part
    )
    links = " | ".join(filter(None, [_link(startup.link, "Website"), _link(startup.source_url, "Source")]))
    text = escape(startup.summary or startup.description or "")
    return f"<b>{escape(startup.name)}</b><br><small>{details}</small><br>{text}<br>{links}"


ESRI_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{layer}/MapServer/tile/{{z}}/{{y}}/{{x}}"
ESRI_ATTRIBUTION = "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"


def build_map(startups: list[Startup]) -> folium.Map:
    # Light grey base map plus a separate layer with English place names (no API key needed).
    # OpenStreetMap tiles would show names in the local language, e.g. Arabic or Chinese.
    world = folium.Map(
        location=[25, 10], zoom_start=2, max_zoom=16, tiles=ESRI_TILES.format(layer="World_Light_Gray_Base"), attr=ESRI_ATTRIBUTION
    )
    folium.TileLayer(
        ESRI_TILES.format(layer="World_Light_Gray_Reference"), attr=ESRI_ATTRIBUTION, name="Labels", overlay=True, control=False
    ).add_to(world)
    cluster = MarkerCluster(name="Startups (city known)").add_to(world)
    by_country: dict[str, list[Startup]] = defaultdict(list)

    for startup in startups:
        if startup.latitude is None or startup.longitude is None:
            continue
        if startup.location_precision == "city":
            folium.Marker(
                [startup.latitude, startup.longitude],
                popup=folium.Popup(_popup_html(startup), max_width=320),
                tooltip=startup.name,
                icon=folium.Icon(color="blue", icon="briefcase", prefix="fa"),
            ).add_to(cluster)
        elif startup.location_precision == "country" and startup.country:
            by_country[startup.country].append(startup)

    for country, group in by_country.items():
        items = "".join(f"<li>{_popup_html(s)}</li>" for s in group)
        folium.Marker(
            [group[0].latitude, group[0].longitude],
            popup=folium.Popup(
                f"<b>{escape(country)}</b> – {len(group)} startup(s), city unknown<ul>{items}</ul>", max_width=360
            ),
            tooltip=f"{country}: {len(group)} startup(s), city unknown",
            icon=folium.Icon(color="orange", icon="flag", prefix="fa"),
        ).add_to(world)

    return world


def save_map(startups: list[Startup], path: Path = MAP_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    build_map(startups).save(str(path))
    return path
