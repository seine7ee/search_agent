"""Shared output-path helpers for trajectory synthesis entry points."""

from __future__ import annotations

import time
from threading import Lock
from pathlib import Path


_TIMESTAMP_LOCK = Lock()
_LAST_TIMESTAMP_MS = -1


def millisecond_timestamp() -> int:
    """Return a process-unique Unix timestamp with millisecond precision."""
    global _LAST_TIMESTAMP_MS

    current = time.time_ns() // 1_000_000
    with _TIMESTAMP_LOCK:
        # Concurrent duplicate queries must not resolve to the same output path.
        _LAST_TIMESTAMP_MS = max(current, _LAST_TIMESTAMP_MS + 1)
        return _LAST_TIMESTAMP_MS


def build_trajectory_filename(
    query: str,
    current_date: str,
    *,
    timestamp_ms: int | None = None,
) -> str:
    """Build the common ``traj_<query>_<date>_<timestamp>.json`` filename."""
    normalized_query = query.strip()
    normalized_date = current_date.strip()
    if not normalized_query:
        raise ValueError("query cannot be empty")
    if not normalized_date:
        raise ValueError("current_date cannot be empty")

    # A query or date containing a slash must remain one filename component.
    safe_query = normalized_query.replace("/", "_").replace("\\", "_")
    safe_date = normalized_date.replace("/", "_").replace("\\", "_")
    value = millisecond_timestamp() if timestamp_ms is None else timestamp_ms
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("timestamp_ms must be a non-negative integer")
    return f"traj_{safe_query}_{safe_date}_{value}.json"


def build_trajectory_path(
    directory: str | Path,
    query: str,
    current_date: str,
    *,
    timestamp_ms: int | None = None,
) -> Path:
    """Build a trajectory path under ``directory`` using the common name."""
    return Path(directory) / build_trajectory_filename(
        query,
        current_date,
        timestamp_ms=timestamp_ms,
    )
