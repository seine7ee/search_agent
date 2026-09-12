import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traj_web_extractor import pipeline
from traj_web_extractor.quote_extractor import (
    QuoteExtractionError, extract_goal_web_quotes, extract_goal_web_quotes_file,
)


class JsonlAppendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "search_goals.json"
        self.destination = self.root / "quotes.jsonl"
        self.web1 = {"web_id": "1", "web_content": '原文甲\n包含"引号"｜竖线', "title": "网页甲"}
        self.web2 = {"web_id": "2", "web_content": "原文乙"}
        self.data = {
            "user_query": "用户问题",
            "search_goals": [
                {"search_goal_id": "G1", "search_goal": "重点一", "webs": [self.web1, self.web2]},
                {"search_goal_id": "G2", "search_goal": "重点二", "webs": [self.web1]},
            ],
        }
        self.source.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")

    def read_records(self, path=None):
        return [json.loads(line) for line in (path or self.destination).read_text(encoding="utf-8").splitlines()]

    def test_each_record_is_appended_and_fsynced_before_next_web(self):
        snapshots = []
        responses = [json.dumps(self.web1["web_content"], ensure_ascii=False), "无相关信息", '"原文甲"']
        seen = []
        with patch("traj_web_extractor.quote_extractor.os.fsync", wraps=os.fsync) as sync, \
             patch("traj_web_extractor.quote_extractor.os.open", wraps=os.open) as open_file:
            def model(messages):
                index = len(seen)
                if index == 0:
                    self.assertFalse(self.destination.exists())
                else:
                    current = self.destination.read_bytes()
                    self.assertEqual(len(self.read_records()), index)
                    self.assertEqual(sync.call_count, index)
                    if snapshots:
                        self.assertTrue(current.startswith(snapshots[-1]))
                    snapshots.append(current)
                seen.append(messages)
                return responses[index]

            results = extract_goal_web_quotes_file(self.source, self.destination, request_model=model)
            self.assertEqual(sync.call_count, 3)
            self.assertEqual(open_file.call_count, 1)
            flags = open_file.call_args.args[1]
            self.assertTrue(flags & os.O_APPEND)
            self.assertTrue(flags & os.O_EXCL)
        self.assertEqual(self.read_records(), results)
        self.assertTrue(self.destination.read_bytes().startswith(snapshots[-1]))
        self.assertEqual(len(self.destination.read_text(encoding="utf-8").splitlines()), 3)
        self.assertEqual([item["search_goal_id"] for item in results], ["G1", "G1", "G2"])
        self.assertEqual(results[0]["quotes"], [self.web1["web_content"]])
        self.assertEqual(results[1]["quotes"], [])

    def test_later_model_failure_keeps_all_completed_lines(self):
        model = Mock(side_effect=['"原文甲"', "无相关信息", RuntimeError("third webpage failed")])
        with self.assertLogs(level="WARNING"), self.assertRaises(QuoteExtractionError):
            extract_goal_web_quotes_file(self.source, self.destination, request_model=model, max_attempts=1)
        records = self.read_records()
        self.assertEqual(len(records), 2)
        self.assertEqual([row["web"]["web_id"] for row in records], ["1", "2"])
        self.assertEqual(records[1]["quotes"], [])
        self.assertTrue(self.destination.read_bytes().endswith(b"\n"))

    def test_retries_write_only_one_record_after_success(self):
        self.data["search_goals"] = [{"search_goal_id": "G1", "search_goal": "重点", "webs": [self.web1]}]
        self.source.write_text(json.dumps(self.data), encoding="utf-8")
        calls = []

        def model(messages):
            self.assertFalse(self.destination.exists())
            calls.append(messages)
            return "invalid format" if len(calls) == 1 else '"原文甲"'

        with self.assertLogs(level="WARNING"):
            extract_goal_web_quotes_file(self.source, self.destination, request_model=model,
                                         max_attempts=2, retry_delay_seconds=0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(self.read_records()), 1)

    def test_first_web_failure_creates_no_result_file(self):
        with self.assertLogs(level="WARNING"), self.assertRaises(QuoteExtractionError):
            extract_goal_web_quotes_file(self.source, self.destination,
                                         request_model=Mock(side_effect=RuntimeError("failed")), max_attempts=1)
        self.assertFalse(self.destination.exists())

    def test_zero_jobs_create_empty_jsonl(self):
        self.source.write_text(json.dumps({"user_query": "问题", "search_goals": []}), encoding="utf-8")
        model = Mock()
        self.assertEqual(extract_goal_web_quotes_file(self.source, self.destination, request_model=model), [])
        self.assertEqual(self.destination.read_bytes(), b"")
        model.assert_not_called()

    def test_progress_callback_runs_only_after_durable_write(self):
        published = []
        with patch("traj_web_extractor.quote_extractor.os.fsync", wraps=os.fsync) as sync:
            def saved(record):
                published.append(record)
                self.assertEqual(len(self.read_records()), len(published))
                self.assertEqual(sync.call_count, len(published))
                record["quotes"].append("callback mutation")

            result = extract_goal_web_quotes_file(self.source, self.destination,
                                                  request_model=Mock(return_value="无相关信息"), on_record_saved=saved)
        self.assertEqual(len(published), 3)
        self.assertTrue(all(record["quotes"] == [] for record in result))
        self.assertEqual(self.read_records(), result)

    def test_fsync_failure_does_not_retry_model_or_duplicate_record(self):
        model = Mock(return_value="无相关信息")
        with patch("traj_web_extractor.quote_extractor.os.fsync", side_effect=OSError("mock disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                extract_goal_web_quotes_file(self.source, self.destination, request_model=model, max_attempts=3)
        model.assert_called_once()

    def test_output_created_by_other_process_is_not_overwritten(self):
        def model(messages):
            self.destination.write_text("existing data\n", encoding="utf-8")
            return "无相关信息"

        with self.assertRaises(FileExistsError):
            extract_goal_web_quotes_file(self.source, self.destination, request_model=model)
        self.assertEqual(self.destination.read_text(encoding="utf-8"), "existing data\n")

    def test_invalid_input_creates_no_output_and_makes_no_requests(self):
        self.data["search_goals"][-1]["webs"] = [{"web_content": None}]
        self.source.write_text(json.dumps(self.data), encoding="utf-8")
        model = Mock()
        with self.assertRaises(ValueError):
            extract_goal_web_quotes_file(self.source, self.destination, request_model=model)
        self.assertFalse(self.destination.exists())
        model.assert_not_called()

    def test_invalid_callbacks_are_rejected(self):
        model = Mock()
        with self.assertRaises(ValueError):
            extract_goal_web_quotes(self.data, request_model=model, on_record=1)
        with self.assertRaises(ValueError):
            extract_goal_web_quotes_file(self.source, self.destination, request_model=model, on_record_saved=1)
        with self.assertRaises(ValueError):
            extract_goal_web_quotes_file(self.source, request_model=model, on_record_saved=lambda row: None)
        model.assert_not_called()

    def test_callback_failure_is_not_a_model_retry(self):
        model = Mock(return_value="无相关信息")
        callback = Mock(side_effect=RuntimeError("callback failed"))
        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            extract_goal_web_quotes(self.data, request_model=model, on_record=callback)
        model.assert_called_once()
        callback.assert_called_once()

    def test_pipeline_reports_partial_path_and_saved_counts(self):
        raw = self.root / "raw.json"
        raw.write_text(json.dumps({"trajectory": {
            "user_query": "问题",
            "goals": [{"search_goal_id": "G1", "search_goal": "重点", "web_ids": ["1", "2"]}],
            "webs": [self.web1, self.web2],
        }}), encoding="utf-8")
        model = Mock(side_effect=['"原文"｜"甲"', RuntimeError("second webpage failed")])
        with patch.object(pipeline, "SEARCH_GOALS_DIR", self.root / "search_goals"), \
             patch.object(pipeline, "WEBS_QUOTES_DIR", self.root / "webs_quotes"), \
             self.assertLogs(level="WARNING"):
            report = pipeline.run_trajectory_pipeline(raw, request_model=model, max_attempts=1)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["failed_stage"], "quotes")
        self.assertIs(report["quotes_complete"], False)
        self.assertEqual(report["record_count"], 1)
        self.assertEqual(report["quote_count"], 2)
        self.assertEqual(Path(report["quotes_path"]).suffix, ".jsonl")
        self.assertTrue(Path(report["search_goals_path"]).is_file())
        self.assertEqual(self.read_records(Path(report["quotes_path"]))[0]["quotes"], ["原文", "甲"])


if __name__ == "__main__":
    unittest.main()
