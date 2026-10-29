"""Step 2: send one small test request to each API and report the result.

Run with:  .venv\\Scripts\\python.exe check_apis.py
"""

from searchbrain import config


def check_nvidia() -> str:
    from openai import OpenAI

    client = OpenAI(
        base_url=config.NVIDIA_BASE_URL, api_key=config.get("NVIDIA_API_KEY"), timeout=30, max_retries=0
    )
    response = client.chat.completions.create(
        model=config.NVIDIA_MODEL,
        messages=[{"role": "user", "content": "Reply with exactly: NVIDIA works"}],
        max_tokens=200,
    )
    return response.choices[0].message.content.strip()


def check_tavily() -> str:
    from tavily import TavilyClient

    client = TavilyClient(api_key=config.get("TAVILY_API_KEY"))
    response = client.search("climate tech startup founded 2024", max_results=3)
    results = response.get("results", [])
    if not results:
        return "Search worked, but returned no results"
    return f"{len(results)} results, first: {results[0]['title']} ({results[0]['url']})"


def check_nominatim() -> str:
    from geopy.geocoders import Nominatim

    geolocator = Nominatim(user_agent=config.get("NOMINATIM_USER_AGENT"), timeout=10)
    location = geolocator.geocode("Berlin, Germany")
    if location is None:
        return "No result for 'Berlin, Germany'"
    return f"Berlin, Germany -> {location.latitude:.4f}, {location.longitude:.4f}"


def main() -> None:
    missing = config.missing_keys()
    if missing:
        print("Missing in .env:", ", ".join(missing))
        return

    for name, check in [("NVIDIA", check_nvidia), ("Tavily", check_tavily), ("Nominatim", check_nominatim)]:
        try:
            print(f"[OK]    {name}: {check()}")
        except Exception as error:
            print(f"[ERROR] {name}: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
