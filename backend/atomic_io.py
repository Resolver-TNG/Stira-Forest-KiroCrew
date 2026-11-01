"""Shared atomic filesystem primitives for Stira.

Only Windows sharing violations are retried.  Every other filesystem error is
raised immediately so configuration and permission failures stay visible.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable, Sequence

WINDOWS_SHARING_WINERRORS = frozenset({5, 32})
REPLACE_RETRY_DELAYS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)


def atomic_replace(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    delays: Sequence[float] = REPLACE_RETRY_DELAYS,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
    sleep: Callable[[float], None] = time.sleep,
    platform: str = os.name,
) -> None:
    """Atomically replace ``destination`` with bounded Windows retry.

    The default schedule waits 2.55 seconds in total (50, 100, 200, 400, 800,
    and 1000 ms) and performs one final replace attempt.  The final sharing
    violation is re-raised unchanged.
    """

    source_path = Path(source)
    destination_path = Path(destination)
    for attempt in range(len(delays) + 1):
        try:
            replace(source_path, destination_path)
            return
        except OSError as exc:
            retryable = (
                platform == "nt"
                and getattr(exc, "winerror", None) in WINDOWS_SHARING_WINERRORS
            )
            if not retryable or attempt == len(delays):
                raise
            sleep(float(delays[attempt]))
