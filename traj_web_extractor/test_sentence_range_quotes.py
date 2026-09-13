from __future__ import annotations

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
from traj_web_extractor.sentence_id_quotes import split_web_content
from traj_web_extractor.sentence_range_quotes import (
    SYSTEM_PROMPT,
    SentenceRangeFormatError,
    build_sentence_range_messages,
    merge_sentence_ranges,
    parse_sentence_ranges,
    rebuild_range_quotes,
)


class SentenceRangePromptTests(unittest.TestCase):
    def test_context_contains_numbered_sentences_and_range_contract(self):
        now = datetime(2026, 9, 13, 9, 15, 0, 456000, tzinfo=timezone.utc)
        messages = build_sentence_range_messages("原问题", "当前目标", "甲。乙！", now=now)

        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)
        self.assertIn("[sent_idx_start, sent_idx_end]", SYSTEM_PROMPT)
        self.assertIn("孤立一句", SYSTEM_PROMPT)
        self.assertIn("不得拆成", SYSTEM_PROMPT)
        self.assertIn("中间只间隔不超过 3 个句子", SYSTEM_PROMPT)
        self.assertIn("2026-09-13T09:15:00.456+00:00", messages[1]["content"])
        self.assertIn("用户query：原问题", messages[1]["content"])
        self.assertIn("当前搜索重点：当前目标", messages[1]["content"])
        self.assertIn(
            "<numbered_web_content>\n[1] 甲。\n[2] 乙！\n</numbered_web_content>",
            messages[1]["content"],
        )

    def test_context_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            build_sentence_range_messages("问题", "目标", "网页", now=datetime(2026, 9, 13))
        for values in ((None, "目标", "网页"), ("问题", None, "网页"), ("问题", "目标", None)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_sentence_range_messages(*values)


class SentenceRangeParsingTests(unittest.TestCase):
    def test_parses_empty_single_and_multiple_ranges(self):
        self.assertEqual(parse_sentence_ranges(" []\n", 8), [])
        self.assertEqual(parse_sentence_ranges("[3, 5]", 8), [[3, 5]])
        self.assertEqual(
            parse_sentence_ranges(" [1, 2] | [6, 6] | [8, 8] ", 8),
            [[1, 2], [6, 8]],
        )

    def test_harness_merges_ranges_with_at_most_two_sentences_between(self):
        self.assertEqual(parse_sentence_ranges("[1, 1]|[2, 2]", 10), [[1, 2]])
        self.assertEqual(parse_sentence_ranges("[1, 1]|[3, 3]", 10), [[1, 3]])
        self.assertEqual(parse_sentence_ranges("[1, 1]|[4, 4]", 10), [[1, 4]])
        self.assertEqual(
            parse_sentence_ranges("[1, 1]|[4, 4]|[7, 7]", 10),
            [[1, 7]],
        )
        self.assertEqual(
            parse_sentence_ranges("[1, 1]|[5, 5]", 10),
            [[1, 1], [5, 5]],
        )
        self.assertEqual(
            merge_sentence_ranges([[2, 3], [6, 7]], max_gap_sentences=2),
            [[2, 7]],
        )

    def test_rejects_malformed_out_of_range_or_ambiguous_ranges(self):
        invalid = (
            None,
            "",
            "无相关信息",
            "```text\n[1, 2]\n```",
            "[[1, 2], [4, 4]]",
            "[1]",
            "[1, 2, 3]",
            "[true, 2]",
            "[1.0, 2]",
            "[0, 1]",
            "[1, 9]",
            "[3, 2]",
            "[1, 3]|[3, 4]",
            "[4, 4]|[2, 2]",
            "[1, 1]｜[3, 3]",
            "[1, 1]|",
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(SentenceRangeFormatError):
                parse_sentence_ranges(response, 8)

    def test_rebuild_uses_exact_inclusive_normalized_spans(self):
        content = "一。 二。\n三。 四。 五。 六。"
        sentences = split_web_content(content)

        self.assertEqual(
            rebuild_range_quotes(content, sentences, [[2, 4], [6, 6]]),
            ["二。 三。 四。 五。 六。"],
        )
        self.assertEqual(rebuild_range_quotes(content, sentences, []), [])


class SentenceRangeExtractionTests(unittest.TestCase):
    def setUp(self):
        self.content = "一。二。三。四。五。六。"
        self.web = {"web_id": "W1", "web_content": self.content, "title": "样例"}
        self.data = {
            "user_query": "问题",
            "search_goals": [
                {"search_goal_id": "G1", "search_goal": "目标", "webs": [self.web]}
            ],
        }

    def test_range_mode_saves_ranges_sentences_and_exact_quotes(self):
        before = copy.deepcopy(self.data)
        model = Mock(return_value="[2, 4]|[6, 6]")

        result = extract_goal_web_quotes(
            self.data,
            request_model=model,
            extraction_mode="sentence_ranges",
        )

        self.assertEqual(result[0]["sentence_ranges"], [[2, 6]])
        self.assertEqual(result[0]["quotes"], ["二。三。四。五。六。"])
        self.assertEqual(len(result[0]["sentences"]), 6)
        self.assertNotIn("sentence_ids", result[0])
        self.assertEqual(self.data, before)
        self.assertIn("[6] 六。", model.call_args.args[0][1]["content"])

    def test_overlapping_ranges_retry_with_identical_context(self):
        model = Mock(side_effect=["[1, 2]|[2, 3]", "[1, 2]"])
        with self.assertLogs(level="WARNING"):
            result = extract_goal_web_quotes(
                self.data,
                request_model=model,
                extraction_mode="sentence_ranges",
                max_attempts=2,
                retry_delay_seconds=0,
            )

        self.assertEqual(result[0]["sentence_ranges"], [[1, 2]])
        self.assertEqual(result[0]["quotes"], ["一。二。"])
        self.assertEqual(model.call_args_list[0].args[0], model.call_args_list[1].args[0])

    def test_range_mode_can_be_selected_as_global_default(self):
        with patch.object(quote_config, "DEFAULT_EXTRACTION_MODE", "sentence_ranges"):
            result = extract_goal_web_quotes(
                self.data,
                request_model=Mock(return_value="[4, 4]"),
            )
        self.assertEqual(result[0]["sentence_ranges"], [[4, 4]])
        self.assertEqual(result[0]["quotes"], ["四。"])

    def test_exhausted_invalid_range_response_keeps_messages(self):
        with self.assertLogs(level="WARNING"), self.assertRaises(QuoteExtractionError) as caught:
            extract_goal_web_quotes(
                self.data,
                request_model=Mock(return_value="not ranges"),
                extraction_mode="sentence_ranges",
                max_attempts=1,
            )
        self.assertIn("<numbered_web_content>", caught.exception.messages[1]["content"])
        self.assertEqual(caught.exception.raw_response, "not ranges")

    def test_range_mode_is_appended_as_one_jsonl_record(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "search_goals.json"
            destination = Path(directory) / "quotes.jsonl"
            source.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            result = extract_goal_web_quotes_file(
                source,
                destination,
                request_model=Mock(return_value="[3, 5]"),
                extraction_mode="sentence_ranges",
            )
            saved = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(saved, result)
        self.assertEqual(saved[0]["sentence_ranges"], [[3, 5]])
        self.assertEqual(saved[0]["quotes"], ["三。四。五。"])

    def test_complete_pipeline_routes_range_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "raw.json"
            source.write_text(
                json.dumps(
                    {
                        "trajectory": {
                            "user_query": "问题",
                            "goals": [
                                {
                                    "search_goal_id": "G1",
                                    "search_goal": "目标",
                                    "web_ids": ["W1"],
                                }
                            ],
                            "webs": [self.web],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.object(pipeline, "SEARCH_GOALS_DIR", root / "search_goals"), patch.object(
                pipeline, "WEBS_QUOTES_DIR", root / "webs_quotes"
            ):
                report = pipeline.run_trajectory_pipeline(
                    source,
                    request_model=Mock(return_value="[2, 4]"),
                    extraction_mode="sentence_ranges",
                )
            saved = [
                json.loads(line)
                for line in Path(report["quotes_path"]).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(report["status"], "SUCCESS")
        self.assertEqual(report["extraction_mode"], "sentence_ranges")
        self.assertEqual(saved[0]["sentence_ranges"], [[2, 4]])
        self.assertEqual(saved[0]["quotes"], ["二。三。四。"])


if __name__ == "__main__":
    unittest.main()
