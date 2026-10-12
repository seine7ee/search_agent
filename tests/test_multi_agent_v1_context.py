"""Tests for deep_search_multi_agent_v1/context.py."""

from __future__ import annotations

import unittest

from deep_search_multi_agent_v1.context import ContextAssembler, format_compressed_web
from deep_search_multi_agent_v1.models import GoalState, SearchState


class FormatCompressedWebTests(unittest.TestCase):
    def test_renders_metadata_and_compressed_content(self):
        web = {
            "web_id": "1",
            "website": "证券时报网",
            "date": "2026-07-09",
            "title": "小米汽车第二产品系列定名澎程",
            "compressed_content": "雷军官宣SkyNomad中文名。",
        }
        text = format_compressed_web(web)
        self.assertEqual(
            text,
            "[1]网页来源: 证券时报网｜网页时间: 2026-07-09｜网页标题: 小米汽车第二产品系列定名澎程"
            "｜网页内容：雷军官宣SkyNomad中文名。",
        )

    def test_includes_authority_level_only_when_present(self):
        web = {
            "web_id": "2",
            "website": "新京报",
            "date": "2026-07-09",
            "title": "标题",
            "authority_level": "高",
            "compressed_content": "内容",
        }
        text = format_compressed_web(web)
        self.assertIn("权威性: 高｜网页标题", text)


class InitialMessagesTests(unittest.TestCase):
    def test_initial_messages_embed_query_and_date(self):
        assembler = ContextAssembler()
        messages = assembler.initial_messages("测试问题", "2026-08-23")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("测试问题", messages[1]["content"])
        self.assertIn("2026-08-23", messages[1]["content"])


class PlannerTurnMessagesTests(unittest.TestCase):
    def _state_with_one_closed_goal(self) -> SearchState:
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(
            search_goal_id="G1",
            search_goal="确认X",
            status="CLOSED",
            supported_statement="X已确认为真。",
        )
        state.goals["G2"] = GoalState(search_goal_id="G2", search_goal="确认Y")
        return state

    def test_turn_webs_render_compressed_content_no_evidence_section(self):
        state = self._state_with_one_closed_goal()
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "search_focus": None,
            "search_queries": ["Y 时间"],
            "compressed_webs": [
                {
                    "web_id": "1",
                    "website": "示例",
                    "date": "2026-01-01",
                    "title": "标题",
                    "compressed_content": "Y发生于2026年。",
                }
            ],
        }
        messages = assembler.planner_turn_messages(state, pending)
        content = messages[1]["content"]
        self.assertIn("Y发生于2026年。", content)
        self.assertNotIn("evidences:", content)
        self.assertIn("next_conflict_id: C1", content)

    def test_search_status_excludes_current_goal_and_only_lists_terminal_goals(self):
        state = self._state_with_one_closed_goal()
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "search_focus": None,
            "search_queries": ["Y 时间"],
            "compressed_webs": [],
        }
        messages = assembler.planner_turn_messages(state, pending)
        content = messages[1]["content"]
        status_block = content.split("<search_status>")[1].split("</search_status>")[0]
        self.assertIn("search_goal_id: G1", status_block)
        self.assertNotIn("G2", status_block)

    def test_search_history_replays_webs_and_optional_conflicts(self):
        state = self._state_with_one_closed_goal()
        state.rounds.append({"round_id": 1, "kind": "init"})
        state.rounds.append(
            {
                "round_id": 2,
                "kind": "turn",
                "target_goal_id": "G1",
                "target_goal_text": "确认X",
                "search_queries": ["X 官方"],
                "compressed_webs": [
                    {
                        "web_id": "1",
                        "website": "示例",
                        "date": "2026-01-01",
                        "title": "标题",
                        "compressed_content": "X已确认为真。",
                    }
                ],
                "conflicts": [],
            }
        )
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "search_focus": None,
            "search_queries": ["Y 时间"],
            "compressed_webs": [],
        }
        messages = assembler.planner_turn_messages(state, pending)
        content = messages[1]["content"]
        history_block = content.split("<search_history>")[1].split("</search_history>")[0]
        self.assertIn("<turn1>", history_block)
        self.assertIn("X已确认为真。", history_block)
        # No conflicts this round: the "conflicts:" subsection must be omitted entirely.
        self.assertNotIn("conflicts:", history_block)

    def test_search_history_includes_conflicts_subsection_when_present(self):
        state = self._state_with_one_closed_goal()
        state.rounds.append(
            {
                "round_id": 2,
                "kind": "turn",
                "target_goal_id": "G1",
                "target_goal_text": "确认X",
                "search_queries": ["X 官方"],
                "compressed_webs": [],
                "conflicts": [
                    {
                        "conflict_id": "C1",
                        "conflict_object": "发布时间",
                        "conflict_statement": "信源不一致",
                    }
                ],
            }
        )
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "search_focus": None,
            "search_queries": [],
            "compressed_webs": [],
        }
        messages = assembler.planner_turn_messages(state, pending)
        content = messages[1]["content"]
        history_block = content.split("<search_history>")[1].split("</search_history>")[0]
        self.assertIn("conflicts:", history_block)
        self.assertIn("- C1: 发布时间 —— 信源不一致", history_block)


if __name__ == "__main__":
    unittest.main()
