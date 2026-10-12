"""Tests for deep_search_multi_agent_v1/orchestrator.py.

Covers the web_info_extractor + planner round protocol: round 1 is
Planner-only and only creates the first goal; every later round runs a
stateless, concurrent Web Info Extractor pass over that round's deduped webs
(dropping pages with nothing relevant) followed by a Planner call that
identifies conflicts (conflict_object/conflict_statement only, no evidence
linkage anywhere in this scheme), applies one search_state_update, and
validates the next action -- including the search-budget and
SUPERSEDED-derivation rules shared with the other schemes.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from deep_search_multi_agent_v1.models import GoalState, SearchState
from deep_search_multi_agent_v1.orchestrator import (
    MultiAgentOrchestrator,
    OrchestratorConfig,
    ProtocolError,
)


def _noop_orchestrator() -> MultiAgentOrchestrator:
    """An orchestrator for exercising helper methods directly, no real run."""
    return MultiAgentOrchestrator(lambda messages: {}, lambda query, top_k=10: [])


def _web(id_suffix: str, content: str) -> dict:
    return {
        "url": f"https://example.com/{id_suffix}",
        "title": f"标题{id_suffix}",
        "website": "示例网站",
        "date": "2026-08-01",
        "content": content,
    }


class StateUpdateValidationTests(unittest.TestCase):
    def test_closed_requires_supported_statement(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X")
        with self.assertRaises(ProtocolError):
            orch._apply_state_update(
                {
                    "search_goal_id": "G1",
                    "status": "CLOSED",
                    "supported_statement": "   ",
                    "blocked_conflict_ids": [],
                },
                state,
                "G1",
                known_conflict_ids=set(),
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
                    "supported_statement": "X已确认。",
                    "blocked_conflict_ids": ["C1"],
                },
                state,
                "G1",
                known_conflict_ids={"C1"},
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
                    "supported_statement": "",
                    "blocked_conflict_ids": [],
                    "info_gap": "仍需更多信息",
                },
                state,
                "G1",
                known_conflict_ids=set(),
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
                    "supported_statement": "",
                    "blocked_conflict_ids": [],
                },
                state,
                "G1",
                known_conflict_ids=set(),
            )

    def test_unresolved_allowed_once_budget_exhausted(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X", search_round_count=3)
        status = orch._apply_state_update(
            {
                "search_goal_id": "G1",
                "status": "UNRESOLVED",
                "supported_statement": "",
                "blocked_conflict_ids": [],
                "info_gap": "仍不确定",
            },
            state,
            "G1",
            known_conflict_ids=set(),
        )
        self.assertEqual(status, "UNRESOLVED")

    def test_no_supported_evidence_ids_field_exists_on_goal(self):
        goal = GoalState(search_goal_id="G1", search_goal="x")
        self.assertNotIn("supported_evidence_ids", goal.to_dict())
        self.assertNotIn("evidence_ids", goal.to_dict())


class NormalizeConflictTests(unittest.TestCase):
    def test_conflict_gets_orchestrator_assigned_id_without_evidence_linkage(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        conflicts = orch._normalize_conflicts(
            [{"conflict_object": "发布时间", "conflict_statement": "信源A与信源B口径不一致"}],
            state,
            "G1",
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["conflict_id"], "C1")
        self.assertEqual(conflicts[0]["search_goal_id"], "G1")
        self.assertNotIn("conflicted_evidence_ids", conflicts[0])

    def test_rejects_conflict_missing_object_or_statement(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        with self.assertRaises(ProtocolError):
            orch._normalize_conflicts([{"conflict_statement": "x"}], state, "G1")
        with self.assertRaises(ProtocolError):
            orch._normalize_conflicts([{"conflict_object": "x"}], state, "G1")


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


class CompressRoundWebsTests(unittest.TestCase):
    def test_irrelevant_webs_are_dropped_and_relevant_ones_kept(self):
        """Exercises the real concurrent extraction path (extraction_concurrency=2)
        against a fake request_model, verifying: (a) webs whose model response
        selects no sentence_ranges are dropped from compressed_webs and counted
        in web_extraction_dropped_count, (b) kept webs carry compressed_content
        and metadata, (c) concurrent calls to _call_model from worker threads
        don't corrupt the shared model_call_count counter.
        """
        thread_names = set()
        lock = threading.Lock()

        def fake_request_model(messages):
            with lock:
                thread_names.add(threading.current_thread().name)
            user_content = messages[1]["content"]
            if "web-relevant" in user_content:
                return {"sentence_ranges": [[1, 1]]}
            return {"sentence_ranges": []}

        orch = MultiAgentOrchestrator(
            fake_request_model,
            lambda query, top_k=10: [],
            config=OrchestratorConfig(max_rounds=6, extraction_concurrency=2),
        )
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X是否成立")
        orch._active_state = state

        pending = {
            "goal_id": "G1",
            "search_focus": None,
            "webs": [
                _web("relevant", "web-relevant 这是相关内容。"),
                _web("irrelevant", "这是完全无关的内容。"),
            ],
        }
        result = orch._compress_round_webs(pending, state, round_id=2)

        self.assertEqual(len(result["compressed_webs"]), 1)
        self.assertEqual(result["compressed_webs"][0]["compressed_content"], "web-relevant 这是相关内容。")
        self.assertEqual(state.metrics.web_extraction_dropped_count, 1)
        self.assertEqual(state.metrics.web_extraction_call_count, 2)
        self.assertEqual(state.metrics.model_call_count, 2)
        self.assertGreaterEqual(len(thread_names), 1)

    def test_no_webs_short_circuits_without_model_calls(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-23")
        state.goals["G1"] = GoalState(search_goal_id="G1", search_goal="确认X")
        orch._active_state = state
        result = orch._compress_round_webs({"goal_id": "G1", "webs": []}, state, round_id=2)
        self.assertEqual(result["compressed_webs"], [])
        self.assertEqual(result["web_extractions"], [])


class EndToEndRunTests(unittest.TestCase):
    def test_two_goal_run_completes_and_persists_trajectory(self):
        call_log = []

        def fake_request_model(messages):
            user_content = messages[1]["content"]
            call_log.append(user_content[:40])
            # Web Info Extractor calls are the only ones with numbered sentences.
            if "numbered_web_content" in user_content:
                if "web-x" in user_content or "web-y" in user_content:
                    return {"sentence_ranges": [[1, 1]]}
                return {"sentence_ranges": []}
            # Planner calls.
            if "这是第 1 轮" in user_content:
                # Round 1: create the first goal.
                return {
                    "action": {
                        "action_reason": "先确认基础事实",
                        "search_action": "CREATE_GOAL",
                        "search_goal_id": "G1",
                        "search_goal": "确认X是否成立",
                        "depends_on_goal_ids": [],
                        "supersedes_goal_id": None,
                        "search_queries": ["X 官方 公告"],
                    }
                }
            if "search_goal_id: G1" in user_content and state_holder.get("G1") != "CLOSED":
                state_holder["G1"] = "CLOSED"
                return {
                    "conflicts": [],
                    "search_state_update": {
                        "search_goal_id": "G1",
                        "search_goal": "确认X是否成立",
                        "status": "CLOSED",
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
                }
            return {
                "conflicts": [],
                "search_state_update": {
                    "search_goal_id": "G2",
                    "search_goal": "确认Y的时间",
                    "status": "CLOSED",
                    "supported_statement": "Y发生于2026年1月。",
                    "blocked_conflict_ids": [],
                    "info_gap": "",
                },
                "action": {
                    "action_reason": "所有必要目标均已闭环",
                    "search_action": "STOP",
                },
            }

        state_holder: dict[str, str] = {}

        def fake_web_search(query, top_k=10):
            if "X" in query:
                return [_web("x", "web-x 关于X的正文。")]
            return [_web("y", "web-y 关于Y的正文。")]

        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "traj.json"
            orch = MultiAgentOrchestrator(
                fake_request_model,
                fake_web_search,
                config=OrchestratorConfig(max_rounds=6, top_k=5, extraction_concurrency=2),
                output_path=output_path,
            )
            state = orch.run("X是否成立，Y发生在什么时候？", current_date="2026-08-23")
            self.assertTrue(output_path.exists())

        self.assertEqual(state.run_status, "COMPLETED")
        self.assertEqual(state.goals["G1"].status, "CLOSED")
        self.assertEqual(state.goals["G2"].status, "CLOSED")
        self.assertEqual(state.goals["G1"].supported_statement, "X已经成立。")
        # No evidence bookkeeping exists anywhere in this scheme's state.
        self.assertNotIn("evidences", state.to_dict())
        turn_rounds = [r for r in state.rounds if r.get("kind") == "turn"]
        self.assertTrue(all("compressed_webs" in r for r in turn_rounds))


if __name__ == "__main__":
    unittest.main()
