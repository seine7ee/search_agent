"""Bocha web-search adapter and response normalization."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

import config.config as cfg

try:
    from .search_cache import get_or_fetch
except ImportError:  # pragma: no cover - direct script execution.
    from search_cache import get_or_fetch


BOCHA_WEB_SEARCH_URL = "https://api.bocha.cn/v1/web-search"
_WEB_PAGE_ID_PATTERN = re.compile(r"#WebPages\.(\d+)$")


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def convert_web_page_id(raw_id: Any, position: int) -> int:
    """Convert ``...#WebPages.0`` to the one-based integer ``1``.

    If Bocha omits or changes the ID shape, the item's zero-based position is
    used as a safe fallback so callers still receive a deterministic integer.
    """
    if isinstance(raw_id, str):
        matched = _WEB_PAGE_ID_PATTERN.search(raw_id)
        if matched:
            return int(matched.group(1)) + 1
    return position + 1


def extract_web_pages(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract and normalize ``data.webPages.value`` from a Bocha response.

    Results whose ``isNavigational`` value is exactly ``True`` are discarded.
    ``False`` and ``null`` are retained and normalized to ``False``.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("Bocha response must be a JSON object")

    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise TypeError("Bocha response data must be a JSON object")
    web_pages = data.get("webPages")
    if not isinstance(web_pages, Mapping):
        raise TypeError("Bocha response data.webPages must be a JSON object")
    values = web_pages.get("value")
    if not isinstance(values, Sequence) or isinstance(
        values, (str, bytes, bytearray)
    ):
        raise TypeError("Bocha response data.webPages.value must be a JSON array")

    results: list[dict[str, Any]] = []
    for position, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise TypeError(
                f"Bocha response data.webPages.value[{position}] must be a JSON object"
            )
        if value.get("isNavigational") is True:
            continue

        website = _as_text(value.get("siteName"))
        date = _as_text(value.get("datePublished"))
        title = _as_text(value.get("name"))
        content = _as_text(value.get("summary"))
        item = {
            "id": convert_web_page_id(value.get("id"), position),
            "url": _as_text(value.get("url")),
            "title": title,
            "content": content,
            "website": website,
            "date": date,
            "isNavigational": False,
            "web_content": (
                f"网页来源: {website}｜网页时间: {date}｜"
                f"网页标题: {title}｜网页内容：{content}"
            ),
        }
        results.append(item)
    return results


def web_search(query: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Call Bocha Web Search and return normalized, non-navigational pages."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    normalized_query = query.strip()

    def fetch_raw_result() -> Mapping[str, Any]:
        api_key = cfg.bocha_api_key
        if not api_key:
            raise RuntimeError("environment variable bocha_search_key1 is not set")
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("requests is required: pip install requests") from exc

        response = requests.post(
            BOCHA_WEB_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": normalized_query,
                "summary": True,
                "count": top_k,
            },
            timeout=(10, 60),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise TypeError("Bocha response must be a JSON object")
        if payload.get("code") != 200:
            raise RuntimeError(
                "Bocha search failed: "
                f"code={payload.get('code')}, msg={payload.get('msg')}"
            )
        extract_web_pages(payload)
        return payload

    response_payload = get_or_fetch("bocha", normalized_query, fetch_raw_result)
    if not isinstance(response_payload, Mapping):
        raise TypeError("cached Bocha response must be a JSON object")
    if response_payload.get("code") != 200:
        raise RuntimeError(
            "Bocha search failed: "
            f"code={response_payload.get('code')}, msg={response_payload.get('msg')}"
        )
    return extract_web_pages(response_payload)[:top_k]


def main() -> int:
    parser = argparse.ArgumentParser(description="调用 Bocha 搜索并输出规范化网页")
    parser.add_argument("--query", help="搜索 query", default="小米汽车的第二款量产车型什么时候发布的")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    try:
        results = web_search(args.query, top_k=args.top_k)
    except (RuntimeError, TypeError, ValueError) as exc:
        parser.exit(1, f"错误: {exc}\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
