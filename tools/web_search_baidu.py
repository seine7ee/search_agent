#!/usr/bin/env python3
"""Baidu AI Search adapter and response normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import config.config as cfg

try:
    from .search_cache import get_or_fetch
except ImportError:  # pragma: no cover - direct script execution.
    from search_cache import get_or_fetch


FIELDS = (
    "id",
    "url",
    "title",
    "date",
    "content",
    "type",
    "website",
    "rerank_score",
    "authority_score",
)


def get_authority_level(score: int | float) -> str:
    """Convert an authority score to its Chinese level label."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("authority_score must be a number")
    if score < 0.3:
        return "低"
    if score <= 0.7:
        return "中"
    return "高"


def _id_sort_key(item: Mapping[str, Any]) -> tuple[int, Any]:
    """Sort numeric IDs numerically, followed by non-numeric IDs as text."""
    item_id = item.get("id")
    if isinstance(item_id, bool):
        return (1, str(item_id))
    if isinstance(item_id, (int, float)):
        return (0, item_id)
    if isinstance(item_id, str):
        try:
            return (0, float(item_id))
        except ValueError:
            return (1, item_id)
    return (2, str(item_id))


def extract_references(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract, sort, and enrich every item in ``data['references']``."""
    if not isinstance(data, Mapping):
        raise TypeError("input data must be a JSON object")

    references = data.get("references")
    if not isinstance(references, Sequence) or isinstance(
        references, (str, bytes, bytearray)
    ):
        raise TypeError("references must be a JSON array")

    extracted: list[dict[str, Any]] = []
    for index, reference in enumerate(references):
        if not isinstance(reference, Mapping):
            raise TypeError(f"references[{index}] must be a JSON object")

        item = {field: reference.get(field) for field in FIELDS}
        try:
            authority_level = get_authority_level(item["authority_score"])
        except TypeError as exc:
            raise TypeError(f"references[{index}].{exc}") from exc

        item["authority_level"] = authority_level
        item["web_content"] = (
            f"网页来源: {item['website']}｜网页时间: {item['date']}｜"
            f"网页标题: {item['title']}｜权威性: {authority_level}｜"
            f"网页内容：{item['content']}"
        )
        extracted.append(item)

    return sorted(extracted, key=_id_sort_key)


def web_search(query: str, top_k: int = 10) -> list[dict[str, Any]]:
    """Search Baidu AI Search and return normalized reference objects."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    normalized_query = query.strip()

    def fetch_raw_result() -> Mapping[str, Any]:
        if not cfg.baidu_api_key:
            raise RuntimeError("environment variable baidu_search_key1 is not set")
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("requests is required: pip install requests") from exc

        response = requests.post(
            "https://qianfan.baidubce.com/v2/ai_search/web_search",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.baidu_api_key}",
            },
            data=json.dumps(
                {
                    "messages": [{"role": "user", "content": normalized_query}],
                    "edition": "standard",
                    "search_source": "baidu_search_v2",
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            timeout=(10, 60),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise TypeError("Baidu response must be a JSON object")
        extract_references(payload)
        return payload

    raw_result = get_or_fetch("baidu", normalized_query, fetch_raw_result)
    if not isinstance(raw_result, Mapping):
        raise TypeError("cached Baidu response must be a JSON object")
    return extract_references(raw_result)[:top_k]

if __name__ == '__main__':
    query1 = "中国 A级车展 定义"
    query1 = "大型车展判定标准 行业惯例"
    query1 = "2025年 国内 A级车展 完整名单 举办时间"
    query1 = "2025 重庆国际车展 举办时间"
    rr = web_search(query1, top_k=10)
    print(rr)
    for item in rr:
        web_content = item["web_content"]
        print(web_content)

