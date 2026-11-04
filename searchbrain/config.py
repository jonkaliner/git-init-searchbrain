"""Load settings and API keys from .env."""

import os

from dotenv import load_dotenv

load_dotenv()

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "openai/gpt-oss-20b")  # extraction and summaries
# The chat needs quick tool calls: in a test on 2026-10-29 this model answered in 27 s instead of 216 s
NVIDIA_CHAT_MODEL = os.getenv("NVIDIA_CHAT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")

# "duckduckgo" needs no key. "tavily" is the optional backup and needs TAVILY_API_KEY.
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "duckduckgo").strip().lower()

# How Searchbrain introduces itself to websites it reads (feeds, pages). robots.txt is matched on the first word.
BOT_NAME = "SearchbrainBot"
BOT_USER_AGENT = f"{BOT_NAME}/0.1 (+https://github.com/jonkaliner/git-init-searchbrain)"

REQUIRED_KEYS = ["NVIDIA_API_KEY", "NOMINATIM_USER_AGENT"]


class ConfigError(Exception):
    """Raised when a required setting is missing in .env."""


def get(name: str) -> str:
    """Return a setting from .env, or raise a clear error if it is empty."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is missing. Please add it to your .env file.")
    return value


def missing_keys() -> list[str]:
    """Return the names of all required settings that are still empty."""
    return [name for name in REQUIRED_KEYS if not os.getenv(name, "").strip()]
