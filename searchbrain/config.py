"""Load settings and API keys from .env."""

import os

from dotenv import load_dotenv

load_dotenv()

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "openai/gpt-oss-20b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

USER_NAME = os.getenv("USER_NAME", "").strip()

REQUIRED_KEYS =["NVIDIA_API_KEY", "TAVILY_API_KEY", "NOMINATIM_USER_AGENT"]


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
