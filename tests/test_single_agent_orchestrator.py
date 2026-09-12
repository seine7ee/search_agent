"""Tests for deep_search_single_agent/orchestrator.py.

Covers the single-agent round protocol: round 1 only creates the first goal;
every later round extracts evidence, applies one search_state_update, and
validates the next action -- including the search-budget and
SUPERSEDED-derivation rules that are unique to this scheme.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from deep_search_single_agent.models import GoalState, SearchState
from deep_search_single_agent.orchestrator import (
    OrchestratorConfig,
    ProtocolError,
    SingleAgentOrchestrator,
)


def _noop_orchestrator() -> SingleAgentOrchestrator:
    """An orchestrator for exercising helper methods directly, no real run."""
    return SingleAgentOrchestrator(lambda messages: {}, lambda query, top_k=10: [])


def _web(id_suffix: str, content: str = "内容") -> dict:
    return {
        "url": f"https://example.com/{id_suffix}",
        "title": f"标题{id_suffix}",
        "date": "2026-08-01",
        "content": content,
    }


class StateUpdateValidationTests(unittest.TestCase):
    def test_closed_requires_supporting_evidence(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X")
        state.evidences = [{"evidence_id": "E1"}]
        with self.assertRaises(ProtocolError):
            orch._apply_state_update(
                {
                    "search_goal_id": "G1",
                    "status": "CLOSED",
                    "supported_evidence_ids": [],
                    "blocked_conflict_ids": [],
                },
                state,
                "G1",
                known_evidence_ids={"E1"},
                known_conflict_ids=set(),
                evidence_id_map={},
            )

    def test_closed_rejects_blocking_conflict(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X")
        with self.assertRaises(ProtocolError):
            orch._apply_state_update(
                {
                    "search_goal_id": "G1",
                    "status": "CLOSED",
                    "supported_evidence_ids": ["E1"],
                    "blocked_conflict_ids": ["C1"],
                },
                state,
                "G1",
                known_evidence_ids={"E1"},
                known_conflict_ids={"C1"},
                evidence_id_map={},
            )

    def test_open_rejected_once_search_budget_exhausted(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        goal = GoalState(search_goal_id="G1", search_goal="确认X", search_round_count=3)
        state.goals["G1"] = goal
        self.assertEqual(goal.max_searches, 3)
        with self.assertRaises(ProtocolError):
            orch._apply_state_update(
                {
                    "search_goal_id": "G1",
                    "status": "OPEN",
                    "supported_evidence_ids": [],
                    "blocked_conflict_ids": [],
                    "info_gap": "仍需更多信息",
                },
                state,
                "G1",
                known_evidence_ids=set(),
                known_conflict_ids=set(),
                evidence_id_map={},
            )

    def test_unresolved_rejected_before_budget_exhausted(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X", search_round_count=1)
        with self.assertRaises(ProtocolError):
            orch._apply_state_update(
                {
                    "search_goal_id": "G1",
                    "status": "UNRESOLVED",
                    "supported_evidence_ids": [],
                    "blocked_conflict_ids": [],
                },
                state,
                "G1",
                known_evidence_ids=set(),
                known_conflict_ids=set(),
                evidence_id_map={},
            )

    def test_unresolved_allowed_once_budget_exhausted(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X", search_round_count=3)
        status = orch._apply_state_update(
            {
                "search_goal_id": "G1",
                "status": "UNRESOLVED",
                "supported_evidence_ids": [],
                "blocked_conflict_ids": [],
                "info_gap": "仍不确定",
            },
            state,
            "G1",
            known_evidence_ids=set(),
            known_conflict_ids=set(),
            evidence_id_map={},
        )
        self.assertEqual(status, "UNRESOLVED")

    def test_terminal_derivation_depth_gets_single_search_budget(self):
        goal = GoalState(search_goal_id="G3", search_goal="x", derivation_depth=2)
        self.assertEqual(goal.max_searches, 1)


class DerivationDepthTests(unittest.TestCase):
    def test_superseding_a_terminal_depth_goal_is_rejected(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G3"] = GoalState(
            search_goal_id="G3", search_goal="x", status="SUPERSEDED", derivation_depth=2
        )
        with self.assertRaises(ProtocolError):
            orch._validate_and_apply_action(
                {
                    "search_action": "CREATE_GOAL",
                    "search_goal_id": "G4",
                    "search_goal": "y",
                    "supersedes_goal_id": "G3",
                    "search_queries": ["q"],
                },
                state,
                "G3",
                "SUPERSEDED",
                known_conflict_ids=set(),
            )

    def test_superseded_status_requires_matching_create_goal_action(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="x", status="SUPERSEDED")
        with self.assertRaises(ProtocolError):
            orch._validate_and_apply_action(
                {"search_action": "SEARCH", "type": "goal", "target_id": "G1", "search_queries": ["q"]},
                state,
                "G1",
                "SUPERSEDED",
                known_conflict_ids=set(),
            )


class StopValidationTests(unittest.TestCase):
    def test_stop_rejected_while_a_goal_is_open(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="x", status="OPEN")
        with self.assertRaises(ProtocolError):
            orch._validate_and_apply_action(
                {"search_action": "STOP", "action_reason": "done"},
                state,
                "G1",
                "OPEN",
                known_conflict_ids=set(),
            )

    def test_stop_allowed_when_no_goal_is_open(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="x", status="CLOSED")
        result = orch._validate_and_apply_action(
            {"search_action": "STOP", "action_reason": "done"},
            state,
            "G1",
            "CLOSED",
            known_conflict_ids=set(),
        )
        self.assertEqual(result, "STOP")


class EndToEndRunTests(unittest.TestCase):
    def test_two_goal_run_completes_and_persists_trajectory(self):
        responses = [
            {
                "action": {
                    "action_reason": "先确认基础事实",
                    "search_action": "CREATE_GOAL",
                    "search_goal_id": "G1",
                    "search_goal": "确认X是否成立",
                    "depends_on_goal_ids": [],
                    "supersedes_goal_id": None,
                    "search_queries": ["X 官方 公告"],
                }
            },
            {
                "evidences": [
                    {
                        "evidence_id": "E1",
                        "statement": "X已经成立。",
                        "quotes": [{"web_id": "1", "quotes": ["X已经成立。"]}],
                        "evidence_summary": "X成立",
                    }
                ],
                "conflicts": [],
                "search_state_update": {
                    "search_goal_id": "G1",
                    "search_goal": "确认X是否成立",
                    "status": "CLOSED",
                    "supported_evidence_ids": ["E1"],
                    "supported_statement": "X已经成立。",
                    "blocked_conflict_ids": [],
                    "info_gap": "",
                },
                "action": {
                    "action_reason": "X已闭环，继续确认并行目标Y",
                    "search_action": "CREATE_GOAL",
                    "search_goal_id": "G2",
                    "search_goal": "确认Y的时间",
                    "depends_on_goal_ids": ["G1"],
                    "supersedes_goal_id": None,
                    "search_queries": ["Y 时间 官方"],
                },
            },
            {
                "evidences": [
                    {
                        "evidence_id": "E2",
                        "statement": "Y发生于2026年1月。",
                        "quotes": [{"web_id": "2", "quotes": ["Y发生于2026年1月。"]}],
                        "evidence_summary": "Y时间",
                    }
                ],
                "conflicts": [],
                "search_state_update": {
                    "search_goal_id": "G2",
                    "search_goal": "确认Y的时间",
                    "status": "CLOSED",
                    "supported_evidence_ids": ["E2"],
                    "supported_statement": "Y发生于2026年1月。",
                    "blocked_conflict_ids": [],
                    "info_gap": "",
                },
                "action": {
                    "action_reason": "所有必要目标均已闭环",
                    "search_action": "STOP",
                },
            },
        ]
        call_count = {"n": 0}

        def fake_request_model(messages):
            index = call_count["n"]
            call_count["n"] += 1
            return responses[index]

        def fake_web_search(query, top_k=10):
            n = call_count["n"]
            return [_web(f"{n}-{query}", content=f"关于 {query} 的网页正文，{query}。")]

        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "traj.json"
            orch = SingleAgentOrchestrator(
                fake_request_model,
                fake_web_search,
                config=OrchestratorConfig(max_rounds=6, top_k=5),
                output_path=output_path,
            )
            state = orch.run("X是否成立，Y发生在什么时候？", current_date="2026-08-23")
            self.assertTrue(output_path.exists())

        self.assertEqual(state.run_status, "COMPLETED")
        self.assertEqual(state.goals["G1"].status, "CLOSED")
        self.assertEqual(state.goals["G2"].status, "CLOSED")
        self.assertEqual(len(state.evidences), 2)

    def test_goal_that_never_closes_is_forced_unresolved_after_budget(self):
        def make_open_response(goal_id: str, goal_text: str, next_query: str):
            return {
                "evidences": [],
                "conflicts": [],
                "search_state_update": {
                    "search_goal_id": goal_id,
                    "search_goal": goal_text,
                    "status": "OPEN",
                    "supported_evidence_ids": [],
                    "supported_statement": "",
                    "blocked_conflict_ids": [],
                    "info_gap": "还没有找到直接证据",
                },
                "action": {
                    "action_reason": "继续搜索",
                    "search_action": "SEARCH",
                    "type": "goal",
                    "target_id": goal_id,
                    "search_focus": "继续核实",
                    "search_queries": [next_query],
                },
            }

        responses = [
            {
                "action": {
                    "action_reason": "起始目标",
                    "search_action": "CREATE_GOAL",
                    "search_goal_id": "G1",
                    "search_goal": "确认一个查无实据的事实",
                    "depends_on_goal_ids": [],
                    "supersedes_goal_id": None,
                    "search_queries": ["q0"],
                }
            },
            make_open_response("G1", "确认一个查无实据的事实", "q1"),
            make_open_response("G1", "确认一个查无实据的事实", "q2"),
            {
                "evidences": [],
                "conflicts": [],
                "search_state_update": {
                    "search_goal_id": "G1",
                    "search_goal": "确认一个查无实据的事实",
                    "status": "UNRESOLVED",
                    "supported_evidence_ids": [],
                    "supported_statement": "",
                    "blocked_conflict_ids": [],
                    "info_gap": "搜索预算已用尽，仍未找到证据",
                },
                "action": {
                    "action_reason": "所有必要目标均已闭环或搁置",
                    "search_action": "STOP",
                },
            },
        ]
        call_count = {"n": 0}

        def fake_request_model(messages):
            index = call_count["n"]
            call_count["n"] += 1
            return responses[index]

        def fake_web_search(query, top_k=10):
            return [_web(f"w{call_count['n']}", content=f"与 {query} 无关的正文")]

        orch = SingleAgentOrchestrator(
            fake_request_model,
            fake_web_search,
            config=OrchestratorConfig(max_rounds=6, top_k=5),
        )
        state = orch.run("一个查无实据的问题", current_date="2026-08-23")

        self.assertEqual(state.run_status, "COMPLETED")
        self.assertEqual(state.goals["G1"].status, "UNRESOLVED")
        self.assertEqual(state.goals["G1"].search_round_count, 3)


if __name__ == "__main__":
    unittest.main()
