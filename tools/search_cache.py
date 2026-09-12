"""Persistent, thread-safe cache for raw search-engine responses."""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4


CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
CACHE_FILENAMES = {
    "baidu": "baidu_search_cache.json",
    "bocha": "bocha_search_cache.json",
}

_ENGINE_LOCKS = {
    engine: threading.RLock() for engine in CACHE_FILENAMES
}
_QUERY_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_QUERY_LOCKS_GUARD = threading.Lock()
_T = TypeVar("_T")


def _normalize_engine(search_engine: str) -> str:
    if not isinstance(search_engine, str):
        raise ValueError("search_engine must be 'baidu' or 'bocha'")
    normalized = search_engine.strip().lower()
    if normalized not in CACHE_FILENAMES:
        raise ValueError(
            f"unsupported search_engine={search_engine!r}; "
            f"expected one of {sorted(CACHE_FILENAMES)}"
        )
    return normalized


def _normalize_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    return query.strip()


def get_cache_path(search_engine: str) -> Path:
    """Return the persistent JSON cache path for a search engine."""
    engine = _normalize_engine(search_engine)
    return CACHE_DIR / CACHE_FILENAMES[engine]


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content) if content.strip() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to read search cache {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RuntimeError(f"search cache {path} must contain a JSON object")
    return dict(data)


def _write_cache(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError(f"failed to write search cache {path}: {exc}") from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _read_entry(search_engine: str, query: str) -> tuple[bool, Any]:
    path = get_cache_path(search_engine)
    with _ENGINE_LOCKS[search_engine]:
        data = _read_cache(path)
        if query not in data:
            return False, None
        return True, copy.deepcopy(data[query])


def _write_entry(search_engine: str, query: str, value: Any) -> None:
    path = get_cache_path(search_engine)
    with _ENGINE_LOCKS[search_engine]:
        data = _read_cache(path)
        data[query] = value
        _write_cache(path, data)


def _get_query_lock(search_engine: str, query: str) -> threading.Lock:
    key = (search_engine, query)
    with _QUERY_LOCKS_GUARD:
        return _QUERY_LOCKS.setdefault(key, threading.Lock())


def get_or_fetch(
    search_engine: str,
    query: str,
    fetch_raw_result: Callable[[], _T],
) -> _T:
    """Return a cached raw response, fetching and persisting it on a miss.

    Cache keys are normalized query strings. A per-engine/query lock prevents
    concurrent batch workers from issuing the same external request more than
    once, while different queries can still be fetched concurrently.
    """
    engine = _normalize_engine(search_engine)
    normalized_query = _normalize_query(query)

    found, cached = _read_entry(engine, normalized_query)
    if found:
        return cached

    with _get_query_lock(engine, normalized_query):
        found, cached = _read_entry(engine, normalized_query)
        if found:
            return cached

        raw_result = fetch_raw_result()
        _write_entry(engine, normalized_query, raw_result)
        return copy.deepcopy(raw_result)
