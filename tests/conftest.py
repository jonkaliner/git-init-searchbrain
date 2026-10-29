"""Shared test setup: tests log to a temp file, not to the real logs/searchbrain.log."""

import tempfile
from pathlib import Path

from searchbrain import log

log.LOG_FILE = Path(tempfile.gettempdir()) / "searchbrain-tests.log"
