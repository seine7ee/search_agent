"""Tests for deep_search_multi_agent_v1/web_info_extractor.py."""

from __future__ import annotations

import unittest

from deep_search_multi_agent_v1.web_info_extractor import (
    WebInfoFormatError,
    build_web_info_messages,
    compress_response,
    split_web_content,
)


class BuildMessagesTests(unittest.TestCase):
    def test_messages_embed_query_intent_and_numbered_sentences(self):
        sentences = split_web_content("第一句。第二句。")
        messages = build_web_info_messages("用户问题", "当前搜索目标", sentences)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        content = messages[1]["content"]
        self.assertIn("用户问题", content)
        self.assertIn("当前搜索目标", content)
        self.assertIn("[1] 第一句。", content)
        self.assertIn("[2] 第二句。", content)

    def test_rejects_empty_search_intent(self):
        sentences = split_web_content("第一句。")
        with self.assertRaises(ValueError):
            build_web_info_messages("q", "  ", sentences)


class CompressResponseTests(unittest.TestCase):
    def setUp(self):
        self.content = "一。二。三。四。五。六。七。八。九。十。"
        self.sentences = split_web_content(self.content)

    def test_empty_ranges_yield_empty_string(self):
        result = compress_response({"sentence_ranges": []}, self.content, self.sentences)
        self.assertEqual(result, "")

    def test_close_ranges_merge_into_one_continuous_span(self):
        # Sentences 3-4 sit between the two selected ranges; the default
        # 2-sentence merge gap should fold everything into one span.
        result = compress_response(
            {"sentence_ranges": [[1, 2], [5, 6]]}, self.content, self.sentences
        )
        self.assertEqual(result, "一。二。三。四。五。六。")
        self.assertNotIn("\n\n", result)

    def test_far_apart_ranges_stay_separated_by_a_blank_line(self):
        result = compress_response(
            {"sentence_ranges": [[1, 1], [9, 10]]}, self.content, self.sentences
        )
        self.assertEqual(result, "一。\n\n九。十。")

    def test_rejects_non_mapping_response(self):
        with self.assertRaises(WebInfoFormatError):
            compress_response(None, self.content, self.sentences)  # type: ignore[arg-type]

    def test_rejects_missing_sentence_ranges_field(self):
        with self.assertRaises(WebInfoFormatError):
            compress_response({}, self.content, self.sentences)

    def test_rejects_malformed_range_item(self):
        with self.assertRaises(WebInfoFormatError):
            compress_response({"sentence_ranges": [[1]]}, self.content, self.sentences)

    def test_rejects_out_of_bounds_range(self):
        with self.assertRaises(WebInfoFormatError):
            compress_response({"sentence_ranges": [[1, 99]]}, self.content, self.sentences)

    def test_rejects_non_integer_bounds(self):
        with self.assertRaises(WebInfoFormatError):
            compress_response({"sentence_ranges": [[1.5, 2]]}, self.content, self.sentences)


if __name__ == "__main__":
    unittest.main()
