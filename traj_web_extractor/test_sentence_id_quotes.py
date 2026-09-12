import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from traj_web_extractor import pipeline, quote_config
from traj_web_extractor.quote_extractor import (
    QuoteExtractionError,
    extract_goal_web_quotes,
    extract_goal_web_quotes_file,
)
from traj_web_extractor.sentence_id_quotes import (
    SYSTEM_PROMPT,
    SentenceIdFormatError,
    build_sentence_id_messages,
    format_numbered_sentences,
    group_sentence_ids,
    normalize_web_content,
    parse_sentence_ids,
    rebuild_quotes,
    split_web_content,
)


class SentenceSplittingTests(unittest.TestCase):
    def test_rule_based_split_preserves_text_and_source_spans(self):
        content = "  标题\r\n第一句。 第二句！“引号？”\n数值3.14没有拆分。 English sentence. Next  "
        normalized = normalize_web_content(content)
        sentences = split_web_content(content)
        self.assertNotIn("\n", normalized)
        self.assertNotIn("\r", normalized)
        self.assertEqual([item.sentence_id for item in sentences], list(range(1, 7)))
        self.assertEqual([item.text for item in sentences], [
            "标题第一句。", "第二句！", "“引号？”", "数值3.14没有拆分。",
            "English sentence.", "Next",
        ])
        for sentence in sentences:
            self.assertEqual(normalized[sentence.start:sentence.end], sentence.text)
        self.assertEqual(
            format_numbered_sentences(sentences),
            "[1] 标题第一句。\n[2] 第二句！\n[3] “引号？”\n"
            "[4] 数值3.14没有拆分。\n[5] English sentence.\n[6] Next",
        )

    def test_word_wrapping_newlines_are_cleaned_before_punctuation_split(self):
        content = (
            "China's\nhumanoid\nrobot\noutput is expected to jump 94% in 2026,\n"
            "a report by TrendForce\nsaid.The\nglobal humanoid robotics industry is ready."
            "Unitree\nRobotics and AgiBot are leading."
        )
        sentences = split_web_content(content)
        self.assertEqual([item.text for item in sentences], [
            "China's humanoid robot output is expected to jump 94% in 2026, "
            "a report by TrendForce said.",
            "The global humanoid robotics industry is ready.",
            "Unitree Robotics and AgiBot are leading.",
        ])
        self.assertTrue(all("\n" not in item.text and "\r" not in item.text for item in sentences))

    def test_names_and_cjk_text_split_only_at_sentence_punctuation(self):
        content = "Figure\nAI与波士顿动\n力推进量产。第二句．第三句｡第四句!第五句？"
        self.assertEqual(normalize_web_content(content),
                         "Figure AI与波士顿动力推进量产。第二句．第三句｡第四句!第五句？")
        self.assertEqual([item.text for item in split_web_content(content)], [
            "Figure AI与波士顿动力推进量产。", "第二句．", "第三句｡", "第四句!", "第五句？",
        ])

    def test_empty_and_blank_content_have_no_numbered_sentences(self):
        self.assertEqual(split_web_content(""), [])
        self.assertEqual(split_web_content(" \n\r\n\t"), [])

    def test_rejects_non_string_content(self):
        with self.assertRaises(ValueError):
            split_web_content(None)


class SentenceIdPromptTests(unittest.TestCase):
    def test_context_contains_numbered_sentences_and_strict_json_contract(self):
        now = datetime(2026, 9, 12, 8, 30, 0, 123000, tzinfo=timezone.utc)
        messages = build_sentence_id_messages("原问题", "当前目标", "甲。乙！", now=now)
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)
        self.assertIn("只输出一个可被 JSON 解析的整数数组", SYSTEM_PROMPT)
        self.assertIn("负向或纠错信息同样相关", SYSTEM_PROMPT)
        self.assertIn("2026-09-12T08:30:00.123+00:00", messages[1]["content"])
        self.assertIn("用户query：原问题", messages[1]["content"])
        self.assertIn("当前搜索重点：当前目标", messages[1]["content"])
        self.assertIn("<numbered_web_content>\n[1] 甲。\n[2] 乙！\n</numbered_web_content>",
                      messages[1]["content"])

    def test_context_rejects_naive_time_and_invalid_fields(self):
        with self.assertRaises(ValueError):
            build_sentence_id_messages("问题", "目标", "网页", now=datetime(2026, 9, 12))
        for values in ((None, "目标", "网页"), ("问题", None, "网页"), ("问题", "目标", None)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_sentence_id_messages(*values)


class SentenceIdParsingTests(unittest.TestCase):
    def test_parses_empty_and_selected_ids(self):
        self.assertEqual(parse_sentence_ids(" []\n", 3), [])
        self.assertEqual(parse_sentence_ids("[1, 3]", 3), [1, 3])

    def test_rejects_non_json_invalid_ids_order_and_duplicates(self):
        invalid = (
            None, "", "无相关信息", "```json\n[1]\n```", "1", "{}", "[0]", "[4]",
            "[2, 1]", "[1, 1]", "[true]", '["1"]', "[1, 2.0]",
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(SentenceIdFormatError):
                parse_sentence_ids(response, 3)

    def test_grouping_fills_one_missing_sentence_but_not_two(self):
        self.assertEqual(group_sentence_ids([]), [])
        self.assertEqual(group_sentence_ids([3, 4, 5]), [(3, 5)])
        self.assertEqual(group_sentence_ids([3, 5]), [(3, 5)])
        self.assertEqual(group_sentence_ids([3, 6]), [(3, 3), (6, 6)])
        self.assertEqual(group_sentence_ids([1, 3, 5, 8]), [(1, 5), (8, 8)])

    def test_rebuild_uses_exact_contiguous_source_spans(self):
        content = "一。 二。\n三。\n四。 五。 六。"
        sentences = split_web_content(content)
        self.assertEqual(rebuild_quotes(content, sentences, [3, 5]), ["三。 四。 五。"])
        self.assertEqual(rebuild_quotes(content, sentences, [3, 6]), ["三。", "六。"])
        self.assertEqual(rebuild_quotes(content, sentences, []), [])


class ConfigurableExtractionTests(unittest.TestCase):
    def setUp(self):
        self.content = "一。二。三。四。五。六。"
        self.web = {"web_id": "W1", "web_content": self.content, "title": "样例"}
        self.data = {
            "user_query": "问题",
            "search_goals": [{"search_goal_id": "G1", "search_goal": "目标", "webs": [self.web]}],
        }

    def test_sentence_mode_keeps_selected_ids_and_builds_merged_quotes(self):
        before = copy.deepcopy(self.data)
        model = Mock(return_value="[3, 5]")
        result = extract_goal_web_quotes(
            self.data, request_model=model, extraction_mode="sentence_ids",
        )
        self.assertEqual(result, [{
            "search_goal_id": "G1",
            "search_goal": "目标",
            "web": self.web,
            "sentences": [
                {"sentence_id": 1, "sentence": "一。"},
                {"sentence_id": 2, "sentence": "二。"},
                {"sentence_id": 3, "sentence": "三。"},
                {"sentence_id": 4, "sentence": "四。"},
                {"sentence_id": 5, "sentence": "五。"},
                {"sentence_id": 6, "sentence": "六。"},
            ],
            "sentence_ids": [3, 5],
            "quotes": ["三。四。五。"],
        }])
        self.assertEqual(self.data, before)
        context = model.call_args.args[0][1]["content"]
        self.assertIn("[3] 三。", context)
        self.assertIn("[6] 六。", context)

    def test_original_mode_remains_available_and_has_original_record_shape(self):
        model = Mock(return_value='"三。"')
        result = extract_goal_web_quotes(
            self.data, request_model=model, extraction_mode="verbatim",
        )
        self.assertEqual(result[0]["quotes"], ["三。"])
        self.assertNotIn("sentence_ids", result[0])
        self.assertIn("<web_content>", model.call_args.args[0][1]["content"])
        self.assertNotIn("<numbered_web_content>", model.call_args.args[0][1]["content"])

    def test_global_default_can_switch_without_changing_call_sites(self):
        with patch.object(quote_config, "DEFAULT_EXTRACTION_MODE", "sentence_ids"):
            result = extract_goal_web_quotes(self.data, request_model=Mock(return_value="[1]"))
        self.assertEqual(result[0]["sentence_ids"], [1])

    def test_invalid_sentence_response_retries_with_same_context(self):
        model = Mock(side_effect=["[99]", "[2]"])
        with self.assertLogs(level="WARNING"):
            result = extract_goal_web_quotes(
                self.data, request_model=model, extraction_mode="sentence_ids",
                max_attempts=2, retry_delay_seconds=0,
            )
        self.assertEqual(result[0]["sentence_ids"], [2])
        self.assertEqual(model.call_args_list[0].args[0], model.call_args_list[1].args[0])

    def test_invalid_mode_fails_before_request(self):
        model = Mock()
        with self.assertRaises(ValueError):
            extract_goal_web_quotes(self.data, request_model=model, extraction_mode="unknown")
        model.assert_not_called()

    def test_sentence_mode_is_written_as_one_jsonl_record(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "search_goals.json"
            destination = Path(directory) / "quotes.jsonl"
            source.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            result = extract_goal_web_quotes_file(
                source, destination, request_model=Mock(return_value="[3, 6]"),
                extraction_mode="sentence_ids",
            )
            saved = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(saved, result)
        self.assertEqual(saved[0]["sentences"][2], {"sentence_id": 3, "sentence": "三。"})
        self.assertEqual(saved[0]["sentence_ids"], [3, 6])
        self.assertEqual(saved[0]["quotes"], ["三。", "六。"])

    def test_complete_pipeline_routes_sentence_mode_and_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "raw.json"
            source.write_text(json.dumps({"trajectory": {
                "user_query": "问题",
                "goals": [{"search_goal_id": "G1", "search_goal": "目标", "web_ids": ["W1"]}],
                "webs": [self.web],
            }}, ensure_ascii=False), encoding="utf-8")
            with patch.object(pipeline, "SEARCH_GOALS_DIR", root / "search_goals"), \
                 patch.object(pipeline, "WEBS_QUOTES_DIR", root / "webs_quotes"):
                report = pipeline.run_trajectory_pipeline(
                    source, request_model=Mock(return_value="[3, 5]"),
                    extraction_mode="sentence_ids",
                )
            saved = [
                json.loads(line)
                for line in Path(report["quotes_path"]).read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(report["status"], "SUCCESS")
        self.assertEqual(report["extraction_mode"], "sentence_ids")
        self.assertEqual(report["record_count"], 1)
        self.assertEqual(report["quote_count"], 1)
        self.assertEqual(len(saved[0]["sentences"]), 6)
        self.assertEqual(saved[0]["sentence_ids"], [3, 5])
        self.assertEqual(saved[0]["quotes"], ["三。四。五。"])

    def test_exhausted_invalid_response_keeps_diagnostic_messages(self):
        with self.assertLogs(level="WARNING"), self.assertRaises(QuoteExtractionError) as caught:
            extract_goal_web_quotes(
                self.data, request_model=Mock(return_value="not json"),
                extraction_mode="sentence_ids", max_attempts=1,
            )
        self.assertIn("<numbered_web_content>", caught.exception.messages[1]["content"])
        self.assertEqual(caught.exception.raw_response, "not json")


if __name__ == "__main__":
    unittest.main()
