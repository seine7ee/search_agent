from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import config.config as cfg
from tools import search_cache
from tools.web_search_baidu import web_search as baidu_web_search
from tools.web_search_bocha import web_search as bocha_web_search


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SearchCacheTests(unittest.TestCase):
    def test_persists_query_to_raw_result_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            search_cache, "CACHE_DIR", Path(directory)
        ):
            calls = 0

            def loader():
                nonlocal calls
                calls += 1
                return {"raw": [1, 2, 3]}

            first = search_cache.get_or_fetch("baidu", "  same query  ", loader)
            second = search_cache.get_or_fetch("baidu", "same query", loader)

            self.assertEqual(first, {"raw": [1, 2, 3]})
            self.assertEqual(second, first)
            self.assertEqual(calls, 1)
            cache_data = json.loads(
                (Path(directory) / "baidu_search_cache.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(cache_data, {"same query": {"raw": [1, 2, 3]}})

    def test_same_query_is_fetched_once_across_threads(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            search_cache, "CACHE_DIR", Path(directory)
        ):
            calls = 0
            calls_lock = threading.Lock()

            def loader():
                nonlocal calls
                with calls_lock:
                    calls += 1
                time.sleep(0.02)
                return {"value": "raw"}

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(
                    executor.map(
                        lambda _: search_cache.get_or_fetch(
                            "bocha", "concurrent query", loader
                        ),
                        range(8),
                    )
                )

            self.assertEqual(calls, 1)
            self.assertEqual(results, [{"value": "raw"}] * 8)

    def test_baidu_adapter_caches_raw_http_response(self):
        payload = {
            "references": [
                {
                    "id": 1,
                    "url": "https://example.com",
                    "title": "title",
                    "date": "2026-08-11",
                    "content": "content",
                    "type": "web",
                    "website": "site",
                    "rerank_score": 1,
                    "authority_score": 0.8,
                }
            ]
        }
        fake_requests = types.ModuleType("requests")
        calls = []

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeResponse(payload)

        fake_requests.post = post
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(search_cache, "CACHE_DIR", Path(directory)),
            patch.object(cfg, "baidu_api_key", "test-key"),
            patch.dict(sys.modules, {"requests": fake_requests}),
        ):
            first = baidu_web_search("baidu query", top_k=1)
            second = baidu_web_search("baidu query", top_k=1)
            cached = json.loads(
                (Path(directory) / "baidu_search_cache.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cached["baidu query"], payload)
        self.assertNotIn("web_content", cached["baidu query"]["references"][0])

    def test_bocha_adapter_caches_raw_http_response(self):
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {
                            "id": "https://api.bochaai.com/v1/#WebPages.0",
                            "name": "title",
                            "url": "https://example.com",
                            "summary": "content",
                            "siteName": "site",
                            "datePublished": "2026-08-11",
                            "isNavigational": False,
                        }
                    ]
                }
            },
        }
        fake_requests = types.ModuleType("requests")
        calls = []

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeResponse(payload)

        fake_requests.post = post
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(search_cache, "CACHE_DIR", Path(directory)),
            patch.object(cfg, "bocha_api_key", "test-key"),
            patch.dict(sys.modules, {"requests": fake_requests}),
        ):
            first = bocha_web_search("bocha query", top_k=1)
            second = bocha_web_search("bocha query", top_k=1)
            cached = json.loads(
                (Path(directory) / "bocha_search_cache.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cached["bocha query"], payload)
        self.assertNotIn(
            "web_content", cached["bocha query"]["data"]["webPages"]["value"][0]
        )


if __name__ == "__main__":
    unittest.main()
