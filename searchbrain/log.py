"""Error log for Searchbrain (the "Monitor" part).

Usage in any module:
    from searchbrain.log import get_logger
    logger = get_logger(__name__)
"""

import logging
import os
from pathlib import Path

LOG_FILE = Path("logs") / "searchbrain.log"
SECRET_SETTINGS = ["NVIDIA_API_KEY", "TAVILY_API_KEY", "GEMINI_API_KEY"]


class HideSecrets(logging.Filter):
    """Replace any API key that ends up in a log message with ***."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for name in SECRET_SETTINGS:
            secret = os.getenv(name, "").strip()
            if secret and secret in message:
                message = message.replace(secret, "***")
        record.msg, record.args = message, None
        return True


def get_logger(name: str = "searchbrain") -> logging.Logger:
    """Return a logger that writes to logs/searchbrain.log."""
    if not name.startswith("searchbrain"):
        # e.g. "__main__" when a module is run with python -m
        name = f"searchbrain.{name.strip('_')}"
    root = logging.getLogger("searchbrain")
    if not root.handlers:
        LOG_FILE.parent.mkdir(exist_ok=True)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(HideSecrets())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return logging.getLogger(name)
