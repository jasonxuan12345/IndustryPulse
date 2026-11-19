"""Atomic file replacement with a bounded retry for transient file sharing locks."""

from __future__ import annotations

import time
from pathlib import Path


def replace_file(source: Path, destination: Path) -> None:
    for attempt in range(5):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * 2**attempt)
