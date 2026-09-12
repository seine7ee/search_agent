import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traj_web_extractor import pipeline
from traj_web_extractor import run_batch_pipeline, run_trajectory_pipeline
from traj_web_extractor import run_batch_extraction as batch_runner
from traj_web_extractor import run_pipeline as single_runner


class TrajectoryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.goals_dir = self.root / "search_goals"
        self.quotes_dir = self.root / "webs_quotes"
        self.patches = [
            patch.object(pipeline, "SEARCH_GOALS_DIR", self.goals_dir),
            patch.object(pipeline, "WEBS_QUOTES_DIR", self.quotes_dir),
            patch("logging.basicConfig"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.data = {
            "trajectory": {
                "user_query": "原始问题",
                "goals": [
                    {"search_goal_id": "G1", "search_goal": "检索重点一", "web_ids": ["2", "1"]},
                    {"search_goal_id": "G2", "search_goal": "检索重点二", "web_ids": ["1"]},
                ],
                "webs": [
                    {"web_id": "1", "web_content": "原文甲", "title": "甲网页"},
                    {"web_id": "2", "web_content": "原文乙", "title": "乙网页"},
                ],
            },
        }

    def write_source(self, name="原始轨迹.json", data=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data if data is None else data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_single_pipeline_saves_both_stages_before_and_after_model(self):
        source = self.write_source()
        original = source.read_bytes()
        observed = []

        def model(messages):
            self.assertEqual(len(list(self.goals_dir.glob("*.json"))), 1)
            quote_files = list(self.quotes_dir.glob("*.jsonl"))
            if not observed:
                self.assertEqual(quote_files, [])
            else:
                self.assertEqual(len(quote_files), 1)
                saved = [json.loads(line) for line in quote_files[0].read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(saved), len(observed))
            observed.append(messages[1]["content"])
            quote = "原文乙" if "原文乙" in messages[1]["content"] else "原文甲"
            return json.dumps(quote, ensure_ascii=False)

        report = run_trajectory_pipeline(source, request_model=model)
        self.assertEqual(report["status"], "SUCCESS")
        self.assertIs(report["quotes_complete"], True)
        self.assertEqual((report["goal_count"], report["web_count"], report["record_count"], report["quote_count"]),
                         (2, 3, 3, 3))
        self.assertIsNone(report["failed_stage"])
        self.assertIsNone(report["error"])
        goals_path, quotes_path = Path(report["search_goals_path"]), Path(report["quotes_path"])
        self.assertEqual(goals_path.parent, self.goals_dir)
        self.assertEqual(quotes_path.parent, self.quotes_dir)
        self.assertTrue(goals_path.name.startswith("原始轨迹__"))
        self.assertEqual(goals_path.stem.removesuffix("_search_goals"), quotes_path.stem.removesuffix("_quotes"))
        middle = json.loads(goals_path.read_text(encoding="utf-8"))
        self.assertEqual(quotes_path.suffix, ".jsonl")
        final = [json.loads(line) for line in quotes_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(middle["user_query"], "原始问题")
        self.assertEqual([goal["search_goal_id"] for goal in middle["search_goals"]], ["G1", "G2"])
        self.assertEqual([web["web_id"] for web in middle["search_goals"][0]["webs"]], ["2", "1"])
        self.assertEqual([record["quotes"] for record in final], [["原文乙"], ["原文甲"], ["原文甲"]])
        self.assertTrue(all(set(record) == {"search_goal_id", "search_goal", "web", "quotes"} for record in final))
        self.assertEqual(len(observed), 3)
        self.assertEqual(source.read_bytes(), original)

    def test_quote_failure_keeps_intermediate_and_has_no_final_path(self):
        source = self.write_source()
        model = Mock(side_effect=RuntimeError("mock model unavailable"))
        with self.assertLogs(level="WARNING"):
            report = run_trajectory_pipeline(source, request_model=model, max_attempts=1)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["failed_stage"], "quotes")
        self.assertEqual(report["error"]["type"], "QuoteExtractionError")
        self.assertTrue(Path(report["search_goals_path"]).is_file())
        self.assertIsNone(report["quotes_path"])
        self.assertIsNone(report["record_count"])
        self.assertEqual(list(self.quotes_dir.glob("*.jsonl")), [])
        self.assertIs(report["quotes_complete"], False)
        model.assert_called_once()

    def test_invalid_raw_json_has_no_model_calls_or_results(self):
        source = self.write_source(data={"trajectory": {"user_query": "问题", "goals": None, "webs": []}})
        model = Mock()
        with self.assertLogs(level="ERROR"):
            report = run_trajectory_pipeline(source, request_model=model)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["failed_stage"], "search_goals")
        self.assertIsNone(report["search_goals_path"])
        self.assertIsNone(report["quotes_path"])
        model.assert_not_called()

    def test_missing_file_and_json_syntax_errors_are_reported(self):
        invalid = self.root / "invalid.json"
        invalid.write_text("{not json", encoding="utf-8")
        with self.assertLogs(level="ERROR"):
            report = run_batch_pipeline([self.root / "missing.json", invalid], request_model=Mock())
        self.assertEqual(report["failed_count"], 2)
        self.assertEqual([result["error"]["type"] for result in report["results"]],
                         ["FileNotFoundError", "JSONDecodeError"])

    def test_path_resolution_failure_does_not_abort_batch(self):
        cyclic = self.root / "cyclic.json"
        cyclic.symlink_to(cyclic)
        source = self.write_source()
        with self.assertLogs(level="ERROR"):
            report = run_batch_pipeline([cyclic, source], request_model=Mock(return_value="无相关信息"))
        self.assertEqual([item["status"] for item in report["results"]], ["FAILED", "SUCCESS"])
        self.assertEqual(report["failed_count"], 1)

    def test_batch_continues_after_file_and_model_failures_in_input_order(self):
        first = self.write_source("first.json")
        last = self.write_source("last.json")
        model = Mock(side_effect=[RuntimeError("first failed"), "无相关信息", "无相关信息", "无相关信息"])
        with self.assertLogs(level="WARNING"):
            report = run_batch_pipeline([first, self.root / "missing.json", last],
                                        request_model=model, max_attempts=1)
        self.assertEqual((report["total_count"], report["success_count"], report["failed_count"]), (3, 1, 2))
        self.assertEqual([item["input_path"] for item in report["results"]],
                         [str(first.resolve()), str((self.root / "missing.json").resolve()), str(last.resolve())])
        self.assertEqual([item["status"] for item in report["results"]], ["FAILED", "FAILED", "SUCCESS"])
        self.assertEqual(report["results"][2]["quote_count"], 0)
        self.assertEqual(len(list(self.goals_dir.glob("*.json"))), 2)
        self.assertEqual(len(list(self.quotes_dir.glob("*.jsonl"))), 1)

    def test_repeated_inputs_and_same_filename_do_not_overwrite(self):
        first, second = self.write_source("a/same.json"), self.write_source("b/same.json")
        report = run_batch_pipeline([first, first, second], request_model=Mock(return_value="无相关信息"))
        self.assertEqual(report["success_count"], 3)
        self.assertEqual(len({result["search_goals_path"] for result in report["results"]}), 3)
        self.assertEqual(len({result["quotes_path"] for result in report["results"]}), 3)
        self.assertEqual(len(list(self.goals_dir.glob("*.json"))), 3)
        self.assertEqual(len(list(self.quotes_dir.glob("*.jsonl"))), 3)

    def test_long_unicode_source_names_have_safe_output_lengths(self):
        source = self.write_source("长" * 80 + ".json")
        report = run_trajectory_pipeline(source, request_model=Mock(return_value="无相关信息"))
        self.assertEqual(report["status"], "SUCCESS")
        for key in ("search_goals_path", "quotes_path"):
            path = Path(report[key])
            self.assertLessEqual(len(path.name.encode("utf-8")), 255)
            self.assertTrue(path.is_file())

    def test_concurrent_files_are_processed_in_parallel_with_ordered_results(self):
        data = {"trajectory": {"user_query": "问题", "goals": [
            {"search_goal_id": "G1", "search_goal": "目标", "web_ids": ["1"]}],
            "webs": [{"web_id": "1", "web_content": "原文"}]}}
        sources = [self.write_source("first.json", data), self.write_source("second.json", data)]
        barrier = threading.Barrier(2, timeout=5)

        def model(messages):
            barrier.wait()
            return '"原文"'

        report = run_batch_pipeline(sources, max_workers=2, request_model=model, max_attempts=1)
        self.assertEqual(report["success_count"], 2)
        self.assertEqual([Path(item["input_path"]).name for item in report["results"]], ["first.json", "second.json"])

    def test_empty_input_list_is_valid_and_has_no_calls(self):
        model = Mock()
        report = run_batch_pipeline([], request_model=model)
        self.assertEqual((report["total_count"], report["success_count"], report["failed_count"]), (0, 0, 0))
        self.assertEqual(report["results"], [])
        model.assert_not_called()

    def test_empty_goals_save_both_empty_results_without_api(self):
        source = self.write_source(data={"trajectory": {"user_query": "问题", "goals": [], "webs": []}})
        model = Mock()
        report = run_trajectory_pipeline(source, request_model=model)
        self.assertEqual(report["status"], "SUCCESS")
        self.assertEqual(report["record_count"], 0)
        self.assertEqual(Path(report["quotes_path"]).read_text(encoding="utf-8"), "")
        self.assertIs(report["quotes_complete"], True)
        model.assert_not_called()

    def test_invalid_options_are_rejected_before_processing(self):
        for kwargs in ({"max_workers": 0}, {"max_workers": True}, {"max_workers": 1.5},
                       {"max_attempts": 0}, {"retry_delay_seconds": -1},
                       {"retry_delay_seconds": float("nan")}, {"request_model": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_batch_pipeline([self.root / "missing.json"], **kwargs)
        for paths in ("one.json", Path("one.json"), [1], {"one.json": 1}):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                run_batch_pipeline(paths)
        self.assertFalse(self.goals_dir.exists())

    def test_batch_runner_uses_configured_list_and_project_relative_paths(self):
        source = self.write_source()
        fake_report = {"total_count": 2, "success_count": 2, "failed_count": 0, "results": []}
        with patch.object(batch_runner, "PROJECT_ROOT", self.root), \
             patch.object(batch_runner, "INPUT_FILES", [source, "another.json"]), \
             patch.object(batch_runner, "MAX_WORKERS", 2), \
             patch.object(batch_runner, "run_batch_pipeline", return_value=fake_report) as runner, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(batch_runner.main(), 0)
        self.assertEqual(runner.call_args.args[0], [source, self.root / "another.json"])
        self.assertEqual(runner.call_args.kwargs["max_workers"], 2)
        self.assertEqual(json.loads(output.getvalue()), fake_report)

    def test_batch_runner_returns_nonzero_on_failure_or_invalid_list(self):
        with patch.object(batch_runner, "INPUT_FILES", []), \
             patch.object(batch_runner, "run_batch_pipeline", return_value={"failed_count": 1}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(batch_runner.main(), 1)
        with patch.object(batch_runner, "INPUT_FILES", "not a list"), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(batch_runner.main(), 1)

    def test_single_runner_has_no_output_path_argument(self):
        with patch.object(sys, "argv", ["run_pipeline", "raw.json"]), \
             patch.object(single_runner, "run_trajectory_pipeline", return_value={"status": "SUCCESS"}) as runner, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(single_runner.main(), 0)
        self.assertEqual(runner.call_args.args, (Path("raw.json"),))
        self.assertNotIn("output_path", runner.call_args.kwargs)

    def test_entry_points_work_directly_outside_project_without_live_requests(self):
        project_root = Path(__file__).resolve().parents[1]
        command = [sys.executable, str(project_root / "traj_web_extractor/run_pipeline.py"), "--help"]
        process = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=True)
        self.assertIn("--max-attempts", process.stdout)
        # Override only the input list at execution time, so no API request occurs.
        batch_path = project_root / "traj_web_extractor/run_batch_extraction.py"
        script = (
            "import runpy; "
            f"namespace=runpy.run_path({str(batch_path)!r}, run_name='offline_check'); "
            "namespace['main'].__globals__['INPUT_FILES']=[]; "
            "raise SystemExit(namespace['main']())"
        )
        process = subprocess.run([sys.executable, "-c", script], cwd=self.root,
                                 capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(process.stdout)["total_count"], 0)


if __name__ == "__main__":
    unittest.main()
