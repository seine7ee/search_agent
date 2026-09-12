import unittest
from unittest.mock import patch

import config.config as cfg
from tools.web_search import get_search_backend, web_search


class WebSearchRouterTests(unittest.TestCase):
    def test_routes_to_baidu_from_config(self):
        with (
            patch.object(cfg, "search_engine", "baidu"),
            patch(
                "tools.web_search_baidu.web_search",
                return_value=[{"provider": "baidu"}],
            ) as backend,
        ):
            result = web_search("query", top_k=3)

        self.assertEqual(result, [{"provider": "baidu"}])
        backend.assert_called_once_with("query", top_k=3)

    def test_routes_to_bocha_from_config(self):
        with (
            patch.object(cfg, "search_engine", "bocha"),
            patch(
                "tools.web_search_bocha.web_search",
                return_value=[{"provider": "bocha"}],
            ) as backend,
        ):
            result = web_search("query", top_k=4)

        self.assertEqual(result, [{"provider": "bocha"}])
        backend.assert_called_once_with("query", top_k=4)

    def test_explicit_engine_overrides_config(self):
        with (
            patch.object(cfg, "search_engine", "baidu"),
            patch(
                "tools.web_search_bocha.web_search",
                return_value=[{"provider": "bocha"}],
            ),
        ):
            result = web_search("query", search_engine="bocha")
        self.assertEqual(result, [{"provider": "bocha"}])

    def test_rejects_unsupported_engine(self):
        with self.assertRaisesRegex(ValueError, "unsupported search_engine"):
            get_search_backend("unknown")


if __name__ == "__main__":
    unittest.main()
