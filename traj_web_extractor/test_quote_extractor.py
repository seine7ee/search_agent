import copy
import json
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from traj_web_extractor.quote_extractor import (
    QuoteExtractionError,
    QuoteFormatError,
    extract_goal_web_quotes,
    extract_goal_web_quotes_file,
    parse_quotes,
)
from traj_web_extractor.quote_prompts import SYSTEM_PROMPT, build_quote_messages


class QuoteParsingTests(unittest.TestCase):
    def test_single_and_multiple_passages(self):
        self.assertEqual(parse_quotes('"第一段"｜"第二段"', "第一段。其他信息。第二段。"),
                         ["第一段", "第二段"])
        self.assertEqual(parse_quotes('"第一段"', "第一段"), ["第一段"])

    def test_quoted_separators_and_escaped_content(self):
        quotes = ['来源｜标题：他说"不是这部电影"。', "路径 C:\\tmp\n下一行"]
        response = "｜".join(json.dumps(quote, ensure_ascii=False) for quote in quotes)
        self.assertEqual(parse_quotes(response, "\n".join(quotes)), quotes)

    def test_literal_newline_and_whitespace_preserved(self):
        self.assertEqual(parse_quotes(' \n" 第一行\n第二行 " \n', " 第一行\n第二行 "),
                         [" 第一行\n第二行 "])

    def test_ascii_separator_compatibility(self):
        self.assertEqual(parse_quotes('"甲" | "乙"', "甲乙"), ["甲", "乙"])

    def test_explicit_no_relevant_information(self):
        self.assertEqual(parse_quotes(" 无相关信息\n", "不相关的网页"), [])

    def test_empty_response_is_not_no_information(self):
        for response in (None, 42, "", "  ", '""', '"   "'):
            with self.subTest(response=response), self.assertRaises(QuoteFormatError):
                parse_quotes(response, "原文")

    def test_rejects_malformed_or_extra_output(self):
        for response in ('["甲"]', '{"quotes":["甲"]}', '解释："甲"', '"甲"\n解释',
                         '"甲"｜', '｜"甲"', '"甲"｜｜"乙"', '"甲" "乙"',
                         '```\n"甲"\n```', '“甲”', '"未闭合'):
            with self.subTest(response=response), self.assertRaises(QuoteFormatError):
                parse_quotes(response, "甲乙")

    def test_rejects_paraphrases_and_stitched_passages(self):
        for response in ('"已量产"', '"计划……量产"', '"计划量产"'):
            with self.subTest(response=response), self.assertRaises(QuoteFormatError):
                parse_quotes(response, "计划于明年实现量产")

    def test_empty_source_can_only_have_no_information(self):
        self.assertEqual(parse_quotes("无相关信息", ""), [])
        with self.assertRaises(QuoteFormatError):
            parse_quotes('"不存在"', "")


class QuotePromptTests(unittest.TestCase):
    def test_required_fields_timestamp_and_exact_web_content(self):
        now = datetime(2026, 9, 12, 8, 30, 0, 123000, tzinfo=timezone.utc)
        messages = build_quote_messages("原问题", "当前目标", "正文\n换行｜原文", now=now)
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)
        user_message = messages[1]["content"]
        self.assertIn("2026-09-12T08:30:00.123+00:00", user_message)
        self.assertIn(str(int(now.timestamp() * 1000)), user_message)
        self.assertIn("用户query：原问题", user_message)
        self.assertIn("当前搜索重点：当前目标", user_message)
        self.assertIn("<web_content>\n正文\n换行｜原文\n</web_content>", user_message)
        self.assertIn("负向或纠错信息同样相关", SYSTEM_PROMPT)
        self.assertIn("不可信", SYSTEM_PROMPT)

    def test_time_is_current_when_not_provided(self):
        before = datetime.now().astimezone()
        user_message = build_quote_messages("问题", "目标", "正文")[1]["content"]
        after = datetime.now().astimezone()
        displayed_time = user_message.split("当前时间：", 1)[1].split("（", 1)[0]
        actual = datetime.fromisoformat(displayed_time)
        self.assertLessEqual(before.timestamp() - 0.001, actual.timestamp())
        self.assertLessEqual(actual.timestamp(), after.timestamp())

    def test_rejects_invalid_fields_and_naive_datetime(self):
        for fields in ((None, "目标", "正文"), ("问题", None, "正文"), ("问题", "目标", None)):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                build_quote_messages(*fields)
        with self.assertRaises(ValueError):
            build_quote_messages("问题", "目标", "正文", now=datetime(2026, 9, 12))


class QuoteExtractionTests(unittest.TestCase):
    def setUp(self):
        self.web1 = {"web_id": "1", "web_content": "原文甲说明电影前提有误。", "tags": ["甲"]}
        self.web2 = {"id": "2", "web_content": "不相关的原文乙。"}
        self.data = {
            "user_query": "用户原始问题",
            "search_goals": [
                {"search_goal_id": "G1", "search_goal": "当前目标一",
                 "webs": [self.web1, self.web2], "web_content_str": "不应传入全部网页拼接内容"},
                {"search_goal_id": "G2", "search_goal": "当前目标二", "webs": [self.web1]},
            ],
        }

    def test_output_shape_order_and_per_web_context_isolation(self):
        model = Mock(side_effect=['"原文甲说明电影前提有误。"', "无相关信息", '"电影前提有误"'])
        before = copy.deepcopy(self.data)
        results = extract_goal_web_quotes(self.data, request_model=model)
        self.assertEqual(results, [
            {"search_goal_id": "G1", "search_goal": "当前目标一", "web": self.web1,
             "quotes": ["原文甲说明电影前提有误。"]},
            {"search_goal_id": "G1", "search_goal": "当前目标一", "web": self.web2, "quotes": []},
            {"search_goal_id": "G2", "search_goal": "当前目标二", "web": self.web1,
             "quotes": ["电影前提有误"]},
        ])
        self.assertEqual(self.data, before)
        contexts = [call.args[0][1]["content"] for call in model.call_args_list]
        self.assertIn(self.web1["web_content"], contexts[0])
        self.assertNotIn(self.web2["web_content"], contexts[0])
        self.assertNotIn("当前目标二", contexts[0])
        self.assertNotIn(self.web1["web_content"], contexts[1])
        self.assertIn("当前目标二", contexts[2])
        self.assertNotIn("不应传入全部网页拼接内容", "\n".join(contexts))
        results[0]["web"]["tags"].append("改动")
        self.assertEqual(self.web1["tags"], ["甲"])
        self.assertEqual(results[2]["web"]["tags"], ["甲"])

    def test_default_model_adapter_uses_existing_req_qwen(self):
        module = types.ModuleType("traj_web_extractor.req_qwen")
        module.req_qwen_model = Mock(return_value="无相关信息")
        with patch.dict(sys.modules, {module.__name__: module}):
            results = extract_goal_web_quotes(self.data)
        self.assertEqual(module.req_qwen_model.call_count, 3)
        self.assertEqual([item["quotes"] for item in results], [[], [], []])

    def test_retries_format_and_transport_failures_with_same_messages(self):
        self.data["search_goals"] = [self.data["search_goals"][0]]
        self.data["search_goals"][0]["webs"] = [self.web1]
        model = Mock(side_effect=[RuntimeError("mock timeout"), '"虚构片段"', '"前提有误"'])
        with self.assertLogs("traj_web_extractor.quote_extractor", level="WARNING"):
            results = extract_goal_web_quotes(self.data, request_model=model, retry_delay_seconds=0)
        self.assertEqual(results[0]["quotes"], ["前提有误"])
        requests = [call.args[0] for call in model.call_args_list]
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[1], requests[2])
        self.assertIsNot(requests[0], requests[1])

    def test_failure_is_not_converted_to_empty_quotes(self):
        model = Mock(return_value='"不存在的内容"')
        with self.assertLogs("traj_web_extractor.quote_extractor", level="WARNING"):
            with self.assertRaises(QuoteExtractionError) as caught:
                extract_goal_web_quotes(self.data, request_model=model, max_attempts=2, retry_delay_seconds=0)
        self.assertEqual(model.call_count, 2)
        self.assertIn("goal=G1, web=1", str(caught.exception))
        self.assertEqual(caught.exception.raw_response, '"不存在的内容"')
        self.assertEqual(caught.exception.messages, model.call_args.args[0])

    def test_request_mutation_does_not_change_retry_context(self):
        self.data["search_goals"][0]["webs"] = [self.web1]
        self.data["search_goals"] = self.data["search_goals"][:1]
        observed = []

        def model(messages):
            observed.append(copy.deepcopy(messages))
            if len(observed) == 1:
                messages[1]["content"] = "被调用方修改"
                raise RuntimeError("mock failure")
            return '"前提有误"'

        with self.assertLogs("traj_web_extractor.quote_extractor", level="WARNING"):
            extract_goal_web_quotes(self.data, request_model=model, retry_delay_seconds=0)
        self.assertEqual(observed[0], observed[1])

    def test_validates_all_webs_before_any_model_call(self):
        model = Mock()
        self.data["search_goals"][-1]["webs"] = [{"web_content": None}]
        with self.assertRaisesRegex(ValueError, r"search_goals\[1\]\.webs\[0\]\.web_content"):
            extract_goal_web_quotes(self.data, request_model=model)
        model.assert_not_called()

    def test_empty_goals_or_webs_make_no_requests(self):
        for goals in ([], [{"search_goal_id": "G1", "search_goal": "目标", "webs": []}]):
            model = Mock()
            self.assertEqual(extract_goal_web_quotes(
                {"user_query": "问题", "search_goals": goals}, request_model=model), [])
            model.assert_not_called()

    def test_empty_web_content_still_has_a_record(self):
        self.data["search_goals"] = [{"search_goal_id": "G1", "search_goal": "目标",
                                      "webs": [{"web_content": ""}]}]
        model = Mock(return_value="无相关信息")
        results = extract_goal_web_quotes(self.data, request_model=model)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["quotes"], [])
        model.assert_called_once()

    def test_invalid_retry_configuration(self):
        for kwargs in ({"max_attempts": 0}, {"max_attempts": True}, {"max_attempts": 1.5},
                       {"retry_delay_seconds": -1}, {"retry_delay_seconds": float("nan")},
                       {"retry_delay_seconds": float("inf")}, {"request_model": "not callable"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                extract_goal_web_quotes(self.data, **kwargs)

    def test_file_round_trip_and_read_only_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "search_goals.json"
            source.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8-sig")
            original = source.read_bytes()
            model = Mock(return_value="无相关信息")
            results = extract_goal_web_quotes_file(source, request_model=model)
            self.assertEqual(len(results), 3)
            self.assertEqual(list(Path(directory).iterdir()), [source])
            destination = Path(directory) / "output" / "quotes.jsonl"
            self.assertEqual(extract_goal_web_quotes_file(source, destination, request_model=model), results)
            self.assertEqual([json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()], results)
            self.assertIn("前提有误", destination.read_text(encoding="utf-8"))
            self.assertEqual(source.read_bytes(), original)

    def test_existing_output_and_input_are_protected_before_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "search_goals.json"
            source.write_text(json.dumps(self.data), encoding="utf-8")
            destination = Path(directory) / "quotes.jsonl"
            destination.write_text("已有内容", encoding="utf-8")
            model = Mock()
            with self.assertRaises(FileExistsError):
                extract_goal_web_quotes_file(source, destination, request_model=model)
            with self.assertRaises(ValueError):
                extract_goal_web_quotes_file(source, source, request_model=model)
            model.assert_not_called()
            self.assertEqual(destination.read_text(encoding="utf-8"), "已有内容")

    def test_failed_run_keeps_completed_jsonl_records(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "search_goals.json"
            source.write_text(json.dumps(self.data), encoding="utf-8")
            destination = Path(directory) / "output" / "quotes.jsonl"
            model = Mock(side_effect=["无相关信息", RuntimeError("mock failure")])
            with self.assertLogs("traj_web_extractor.quote_extractor", level="WARNING"):
                with self.assertRaises(QuoteExtractionError):
                    extract_goal_web_quotes_file(source, destination, request_model=model, max_attempts=1)
            records = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records, [{
                "search_goal_id": "G1", "search_goal": "当前目标一", "web": self.web1, "quotes": [],
            }])

    def test_runner_supports_module_and_direct_script_help_without_api(self):
        project_root = Path(__file__).resolve().parents[1]
        commands = [
            [sys.executable, "-m", "traj_web_extractor.run_quote_extraction", "--help"],
            [sys.executable, str(project_root / "traj_web_extractor/run_quote_extraction.py"), "--help"],
        ]
        for command in commands:
            with self.subTest(command=command):
                process = subprocess.run(command, cwd=project_root, capture_output=True, text=True, check=True)
                self.assertIn("--max-attempts", process.stdout)


if __name__ == "__main__":
    unittest.main()
