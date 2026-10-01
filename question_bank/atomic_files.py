"""Atomic file replacement for question-bank state and published assets."""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace_with_retry(source: Path, destination: Path) -> None:
    """Replace atomically, retrying temporary Windows file scanner locks.

    Only permission errors are retried. After 12 attempts (3 seconds of total
    waiting), the original error is raised; callers own temporary-file cleanup.
    """
    for attempt in range(12):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.4))
