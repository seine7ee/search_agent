"""Tests for deep_search_single_agent/context.py."""

from __future__ import annotations

import unittest

from deep_search_single_agent.context import ContextAssembler
from deep_search_single_agent.models import GoalState, SearchState


class InitialMessagesTests(unittest.TestCase):
    def test_initial_messages_embed_query_and_date(self):
        assembler = ContextAssembler()
        messages = assembler.initial_messages("测试问题", "2026-08-23")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("测试问题", messages[1]["content"])
        self.assertIn("2026-08-23", messages[1]["content"])


class TurnMessagesTests(unittest.TestCase):
    def _state_with_one_closed_goal(self) -> SearchState:
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(
            search_goal_id="G1",
            search_goal="确认X",
            status="CLOSED",
            supported_evidence_ids=["E1"],
            supported_statement="X已确认为真。",
        )
        state.goals["G2"] = GoalState(search_goal_id="G2", search_goal="确认Y")
        state.evidences.append({"evidence_id": "E1", "search_goal_id": "G1"})
        return state

    def test_search_status_excludes_current_goal_and_only_lists_terminal_goals(self):
        state = self._state_with_one_closed_goal()
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "conflict": None,
            "search_focus": None,
            "search_queries": ["Y 时间"],
            "webs": [{"web_id": "1", "web_content": "Y发生于2026年。"}],
        }
        messages = assembler.turn_messages(state, pending)
        content = messages[1]["content"]
        # G1 (terminal, CLOSED) appears in search_status.
        self.assertIn("search_goal_id: G1", content)
        self.assertIn("X已确认为真。", content)
        # G2 is the goal under evaluation this round; it must not appear in
        # search_status since search_status only holds frozen goals.
        status_block = content.split("<search_status>")[1].split("</search_status>")[0]
        self.assertNotIn("G2", status_block)
        # id_allocation must point past the one existing evidence.
        self.assertIn("next_evidence_id: E2", content)
        self.assertIn("next_conflict_id: C1", content)

    def test_turn_conflict_block_included_when_pending_targets_a_conflict(self):
        state = self._state_with_one_closed_goal()
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "conflict": {
                "conflict_id": "C1",
                "conflict_object": "Y的发布时间",
                "conflict_statement": "信源A与信源B口径不一致",
                "conflicted_evidence_ids": ["E1"],
            },
            "search_focus": "核实Y的准确时间",
            "search_queries": ["Y 官方 时间"],
            "webs": [],
        }
        messages = assembler.turn_messages(state, pending)
        content = messages[1]["content"]
        self.assertIn("<turn_conflict>", content)
        self.assertIn("conflict_id: C1", content)
        self.assertIn("核实Y的准确时间", content)

    def test_search_history_replays_only_completed_turn_rounds(self):
        state = self._state_with_one_closed_goal()
        state.rounds.append({"round_id": 1, "kind": "init"})
        state.rounds.append(
            {
                "round_id": 2,
                "kind": "turn",
                "target_goal_id": "G1",
                "target_goal_text": "确认X",
                "search_queries": ["X 官方"],
                "evidences": [{"evidence_id": "E1", "statement": "X已确认为真。"}],
                "conflicts": [],
            }
        )
        assembler = ContextAssembler()
        pending = {
            "goal_id": "G2",
            "conflict": None,
            "search_focus": None,
            "search_queries": ["Y 时间"],
            "webs": [],
        }
        messages = assembler.turn_messages(state, pending)
        content = messages[1]["content"]
        history_block = content.split("<search_history>")[1].split("</search_history>")[0]
        self.assertIn("<turn1>", history_block)
        self.assertNotIn("<turn2>", history_block)
        self.assertIn("X已确认为真。", history_block)


if __name__ == "__main__":
    unittest.main()
