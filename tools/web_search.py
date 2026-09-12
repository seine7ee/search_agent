"""Configured web-search backend router."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import config.config as cfg


SUPPORTED_SEARCH_ENGINES = {"baidu", "bocha"}
SearchBackend = Callable[[str, int], list[dict[str, Any]]]


def get_search_backend(search_engine: str | None = None) -> SearchBackend:
    """Return the backend selected explicitly or by ``config.search_engine``."""
    configured = search_engine if search_engine is not None else cfg.search_engine
    if not isinstance(configured, str):
        raise ValueError("search_engine must be 'baidu' or 'bocha'")
    normalized = configured.strip().lower()

    if normalized == "baidu":
        from .web_search_baidu import web_search as backend

        return backend
    if normalized == "bocha":
        from .web_search_bocha import web_search as backend

        return backend
    raise ValueError(
        f"unsupported search_engine={configured!r}; "
        f"expected one of {sorted(SUPPORTED_SEARCH_ENGINES)}"
    )


def web_search(
    query: str,
    top_k: int = 10,
    *,
    search_engine: str | None = None,
) -> list[dict[str, Any]]:
    """Search with the configured backend using a stable common interface."""
    backend = get_search_backend(search_engine)
    return backend(query, top_k=top_k)
