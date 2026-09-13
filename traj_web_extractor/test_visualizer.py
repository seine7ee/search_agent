from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from traj_web_extractor.visualizer import (
    ViewerDataError,
    create_server,
    find_search_goals_file,
    load_view_data,
    scan_quotes_files,
)


class VisualizerDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.quotes_dir = self.root / "webs_quotes"
        self.goals_dir = self.root / "search_goals"
        self.quotes_dir.mkdir()
        self.goals_dir.mkdir()
        self.quotes_path = self.quotes_dir / "demo_quotes.jsonl"
        self.goals_path = self.goals_dir / "demo_search_goals.json"
        self.goals_path.write_text(
            json.dumps({"user_query": "测试问题？", "search_goals": []}, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_records(self, records: list[dict]) -> None:
        self.quotes_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

    @staticmethod
    def record(goal_id: str = "G1", goal: str = "核验事件") -> dict:
        return {
            "search_goal_id": goal_id,
            "search_goal": goal,
            "web": {
                "web_id": "W1",
                "title": "示例网页",
                "website": "example.com",
                "date": "2026-09-12",
                "url": "https://example.com/a",
                "web_content": "第一句。第二句。第三句。",
            },
            "sentences": [
                {"sentence_id": 1, "sentence": "第一句。"},
                {"sentence_id": 2, "sentence": "第二句。"},
                {"sentence_id": 3, "sentence": "第三句。"},
            ],
            "sentence_ids": [1, 3],
            "quotes": ["第一句。第二句。第三句。"],
        }

    def test_resolves_companion_and_builds_grouped_summary(self) -> None:
        self.write_records([self.record(), self.record("G2", "补充厂商信息")])

        self.assertEqual(find_search_goals_file(self.quotes_path), self.goals_path.resolve())
        data = load_view_data(self.quotes_path)

        self.assertEqual(data["user_query"], "测试问题？")
        self.assertEqual([goal["search_goal_id"] for goal in data["goals"]], ["G1", "G2"])
        self.assertEqual(data["summary"]["goal_count"], 2)
        self.assertEqual(data["summary"]["web_count"], 2)
        self.assertEqual(data["summary"]["sentence_count"], 6)
        self.assertEqual(data["summary"]["selected_sentence_count"], 4)
        self.assertEqual(data["summary"]["quote_count"], 2)
        self.assertEqual(data["goals"][0]["records"][0]["record_index"], 1)

    def test_reports_malformed_jsonl_line(self) -> None:
        self.quotes_path.write_text(
            json.dumps(self.record(), ensure_ascii=False) + "\n{not-json}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ViewerDataError, "line 2"):
            load_view_data(self.quotes_path)

    def test_rejects_inconsistent_goal_text(self) -> None:
        self.write_records([self.record(), self.record("G1", "另一个目标")])

        with self.assertRaisesRegex(ViewerDataError, "inconsistent"):
            load_view_data(self.quotes_path)

    def test_scan_only_returns_direct_jsonl_files(self) -> None:
        self.write_records([self.record()])
        another = self.quotes_dir / "another.JSONL"
        another.write_text("", encoding="utf-8")
        (self.quotes_dir / "ignored.txt").write_text("ignored", encoding="utf-8")
        nested = self.quotes_dir / "nested"
        nested.mkdir()
        (nested / "nested.jsonl").write_text("", encoding="utf-8")

        paths = scan_quotes_files(self.quotes_dir)

        self.assertEqual({path.name for path in paths}, {"demo_quotes.jsonl", "another.JSONL"})

    def test_preserves_sentence_ranges_for_range_visualization(self) -> None:
        record = self.record()
        record.pop("sentence_ids")
        record["sentence_ranges"] = [[1, 2]]
        record["quotes"] = ["第一句。第二句。"]
        self.write_records([record])

        data = load_view_data(self.quotes_path)
        saved = data["goals"][0]["records"][0]

        self.assertEqual(saved["selection_mode"], "sentence_ranges")
        self.assertEqual(saved["sentence_ranges"], [[1, 2]])
        self.assertEqual(data["summary"]["selected_sentence_count"], 2)
        self.assertEqual(data["summary"]["range_count"], 1)
        self.assertTrue(data["goals"][0]["uses_sentence_ranges"])


class VisualizerServerTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.server = create_server(
                {
                    "user_query": "接口测试",
                    "source_name": "test.jsonl",
                    "source_file": "/tmp/test.jsonl",
                    "warnings": [],
                    "summary": {
                        "goal_count": 0,
                        "web_count": 0,
                        "sentence_count": 0,
                        "selected_sentence_count": 0,
                        "quote_count": 0,
                        "empty_quote_web_count": 0,
                    },
                    "goals": [],
                },
                port=0,
            )
        except PermissionError:
            self.skipTest("current sandbox does not permit binding a localhost test port")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_serves_page_assets_and_data(self) -> None:
        with urlopen(self.base_url + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Quote Trace", body)
        with urlopen(self.base_url + "/assets/app.js", timeout=2) as response:
            self.assertIn('fetchJson("/api/files")', response.read().decode("utf-8"))
        with urlopen(self.base_url + "/api/files", timeout=2) as response:
            catalog = json.load(response)
            self.assertEqual(catalog["selected_file"], "test.jsonl")
            self.assertEqual([item["name"] for item in catalog["files"]], ["test.jsonl"])
        with urlopen(self.base_url + "/api/data", timeout=2) as response:
            payload = json.load(response)
            self.assertEqual(payload["user_query"], "接口测试")
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_unknown_route_is_not_exposed(self) -> None:
        with self.assertRaises(HTTPError) as captured:
            urlopen(self.base_url + "/../visualizer.py", timeout=2)
        self.assertEqual(captured.exception.code, 404)
        captured.exception.close()


class VisualizerDirectoryServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.quotes_dir = root / "webs_quotes"
        self.goals_dir = root / "search_goals"
        self.quotes_dir.mkdir()
        self.goals_dir.mkdir()
        self.first_name = "first_quotes.jsonl"
        self.second_name = "second_quotes.jsonl"
        for stem, query in (("first", "第一个问题"), ("second", "第二个问题")):
            record = VisualizerDataTests.record(goal_id=stem.upper(), goal=f"{stem}目标")
            (self.quotes_dir / f"{stem}_quotes.jsonl").write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (self.goals_dir / f"{stem}_search_goals.json").write_text(
                json.dumps({"user_query": query}, ensure_ascii=False),
                encoding="utf-8",
            )
        try:
            self.server = create_server(
                quotes_dir=self.quotes_dir,
                selected_file=self.second_name,
                port=0,
            )
        except PermissionError:
            self.temporary_directory.cleanup()
            self.skipTest("current sandbox does not permit binding a localhost test port")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_catalog_default_and_file_switching(self) -> None:
        with urlopen(self.base_url + "/api/files", timeout=2) as response:
            catalog = json.load(response)
        self.assertEqual(catalog["selected_file"], self.second_name)
        self.assertEqual({item["name"] for item in catalog["files"]}, {self.first_name, self.second_name})

        with urlopen(self.base_url + "/api/data", timeout=2) as response:
            self.assertEqual(json.load(response)["user_query"], "第二个问题")
        first_url = self.base_url + "/api/data?file=" + quote(self.first_name)
        with urlopen(first_url, timeout=2) as response:
            first = json.load(response)
        self.assertEqual(first["user_query"], "第一个问题")
        self.assertEqual(first["goals"][0]["search_goal_id"], "FIRST")

    def test_rejects_unscanned_file_selection(self) -> None:
        url = self.base_url + "/api/data?file=" + quote("../outside.jsonl")
        with self.assertRaises(HTTPError) as captured:
            urlopen(url, timeout=2)
        self.assertEqual(captured.exception.code, 404)
        captured.exception.close()


if __name__ == "__main__":
    unittest.main()
