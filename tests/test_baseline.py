from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deep_search_baseline.orchestrator import (
    BaselineConfig,
    BaselineOrchestrator,
    BaselineProtocolError,
)


def fake_web_search(query, top_k=10):
    return [
        {
            "id": 1,
            "url": f"https://example.com/{query}",
            "title": "原始标题不应单独进入历史 Prompt",
            "content": "raw-content-must-not-appear",
            "website": "示例",
            "date": "2026-01-01",
            "web_content": f"网页来源: 示例｜网页标题: {query}｜网页内容：已组装正文",
        }
    ][:top_k]


class SearchThenStopTeacher:
    def __init__(self):
        self.messages = []

    def __call__(self, messages):
        self.messages.append(messages)
        if len(self.messages) == 1:
            return {
                "search_actions": [
                    {
                        "action_reason": "需要两个互补查询",
                        "search_goal": "确认测试事实",
                        "search_queries": ["query-a", "query-b"],
                    }
                ]
            }
        return {
            "search_action": "STOP",
            "action_reason": "信息已经闭环",
        }


class AlwaysSearchTeacher:
    def __call__(self, _messages):
        return {
            "search_actions": [
                {
                    "action_reason": "继续搜索",
                    "search_goal": "尚未闭环的事实",
                    "search_queries": ["query"],
                }
            ]
        }


class BaselineOrchestratorTests(unittest.TestCase):
    def test_search_then_model_stop_with_metrics_and_history(self):
        teacher = SearchThenStopTeacher()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trajectory.json"
            state = BaselineOrchestrator(
                teacher,
                fake_web_search,
                config=BaselineConfig(max_turns=4, top_k=2),
                output_path=output,
            ).run("测试问题", current_date="2026-08-09")

            self.assertEqual(state.run_status, "COMPLETED")
            self.assertEqual(state.metrics.total_turns, 2)
            self.assertEqual(state.metrics.search_turns, 1)
            self.assertEqual(state.metrics.model_call_count, 2)
            self.assertEqual(state.metrics.search_action_count, 1)
            self.assertEqual(state.metrics.search_query_count, 2)
            self.assertEqual(state.metrics.web_result_count, 2)
            self.assertEqual(state.metrics.unique_web_count, 2)
            self.assertEqual(state.metrics.termination_type, "MODEL_STOP")
            self.assertEqual(state.metrics.final_action, "STOP")
            self.assertEqual(state.metrics.last_model_action, "STOP")
            self.assertTrue(state.metrics.to_dict()["terminated_by_model"])
            self.assertFalse(state.metrics.to_dict()["reached_max_turns"])

            second_prompt = teacher.messages[1][1]["content"]
            self.assertIn("[1]网页来源: 示例", second_prompt)
            self.assertIn("[2]网页来源: 示例", second_prompt)
            self.assertNotIn("https://example.com", second_prompt)
            self.assertNotIn("raw-content-must-not-appear", second_prompt)
            self.assertNotIn("evidence_id", second_prompt)

            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["trajectory"]["metrics"]["termination_type"],
                "MODEL_STOP",
            )
            self.assertEqual(len(saved["trajectory"]["turns"]), 2)
            self.assertEqual(
                len(saved["trajectory"]["turns"][0]["teacher"]["messages"]),
                2,
            )
            self.assertEqual(
                len(saved["trajectory"]["turns"][1]["teacher"]["messages"]),
                2,
            )

    def test_max_turns_termination_metrics(self):
        state = BaselineOrchestrator(
            AlwaysSearchTeacher(),
            fake_web_search,
            config=BaselineConfig(max_turns=2),
        ).run("测试问题")

        self.assertEqual(state.run_status, "MAX_TURNS_REACHED")
        self.assertEqual(state.metrics.total_turns, 2)
        self.assertEqual(state.metrics.search_turns, 2)
        self.assertEqual(state.metrics.termination_type, "MAX_TURNS")
        self.assertEqual(state.metrics.final_action, "MAX_TURNS_REACHED")
        self.assertEqual(state.metrics.last_model_action, "SEARCH")
        self.assertFalse(state.metrics.to_dict()["terminated_by_model"])
        self.assertTrue(state.metrics.to_dict()["reached_max_turns"])

    def test_invalid_decision_is_rejected_and_persisted(self):
        def invalid_teacher(_messages):
            return {"answer": "不允许直接回答"}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed.json"
            orchestrator = BaselineOrchestrator(
                invalid_teacher, fake_web_search, output_path=output
            )
            with self.assertRaises(BaselineProtocolError):
                orchestrator.run("测试问题")
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["trajectory"]["run_status"], "FAILED")
            self.assertEqual(
                saved["trajectory"]["metrics"]["termination_type"], "FAILED"
            )
            last_call = saved["trajectory"]["errors"][-1]["last_model_call"]
            self.assertEqual(len(last_call["messages"]), 2)
            self.assertEqual(last_call["status"], "SUCCEEDED")
            self.assertEqual(
                json.dumps(last_call, ensure_ascii=False).count('"messages"'),
                1,
            )

    def test_failed_model_request_persists_messages_once(self):
        def failed_teacher(_messages):
            raise RuntimeError("temporary model failure")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed-request.json"
            orchestrator = BaselineOrchestrator(
                failed_teacher,
                fake_web_search,
                output_path=output,
            )
            with self.assertRaisesRegex(RuntimeError, "model failure"):
                orchestrator.run("测试问题")

            saved = json.loads(output.read_text(encoding="utf-8"))
            last_call = saved["trajectory"]["errors"][-1]["last_model_call"]
            self.assertEqual(last_call["status"], "FAILED")
            self.assertEqual(last_call["attempt_count"], 1)
            self.assertEqual(len(last_call["messages"]), 2)
            self.assertEqual(len(last_call["attempts"]), 1)
            self.assertNotIn("messages", last_call["attempts"][0])
            self.assertEqual(
                json.dumps(last_call, ensure_ascii=False).count('"messages"'),
                1,
            )


if __name__ == "__main__":
    unittest.main()
