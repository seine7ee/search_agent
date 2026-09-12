"""Tests for the conflict dedup/resolution feature in deep_search_claude.

Covers three additions to deep_search_claude/orchestrator.py:
* ``_apply_conflict_status`` labels a touched goal's conflicts SOLVING (a
  VERIFY_CONFLICT targets them next turn) or NON_BLOCKING (it doesn't).
* ``_normalize_evidence`` accepts a ``conflict_updates`` branch from the
  evidence processor that attaches new evidence to an already-tracked
  conflict instead of minting a duplicate conflict_id.
* An end-to-end run confirms the same underlying disagreement, encountered
  again in a later turn, ends up as one conflict with accumulated evidence
  rather than two near-identical conflicts.
"""

from __future__ import annotations

import unittest

from deep_search_claude.orchestrator import (
    DeepSearchOrchestrator,
    OrchestratorConfig,
    ProtocolError,
)
from deep_search_claude.models import SearchState


def _noop_orchestrator() -> DeepSearchOrchestrator:
    """An orchestrator for exercising helper methods directly, no real run."""
    return DeepSearchOrchestrator(lambda messages: {}, lambda query, top_k=10: [])


class ConflictStatusTests(unittest.TestCase):
    def test_apply_conflict_status_labels_solving_and_non_blocking(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        state.conflicts = [
            {"conflict_id": "C1", "search_goal_id": "GOAL_1", "conflict_status": "UNCLASSIFIED"},
            {"conflict_id": "C2", "search_goal_id": "GOAL_1", "conflict_status": "UNCLASSIFIED"},
            {"conflict_id": "C3", "search_goal_id": "GOAL_2", "conflict_status": "UNCLASSIFIED"},
        ]
        next_actions = [
            {
                "search_action": "VERIFY_CONFLICT",
                "target_goal_id": "GOAL_1",
                "target_conflict_id": "C1",
                "action_reason": "核实",
                "search_queries": ["q"],
            }
        ]

        orch._apply_conflict_status(state, {"GOAL_1"}, next_actions)

        statuses = {c["conflict_id"]: c["conflict_status"] for c in state.conflicts}
        self.assertEqual(statuses["C1"], "SOLVING")
        self.assertEqual(statuses["C2"], "NON_BLOCKING")
        # GOAL_2 was not touched this turn: its conflict is left untouched.
        self.assertEqual(statuses["C3"], "UNCLASSIFIED")

    def test_apply_conflict_status_relabels_on_every_call_not_once(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        state.conflicts = [
            {"conflict_id": "C1", "search_goal_id": "GOAL_1", "conflict_status": "SOLVING"}
        ]
        # This round the planner did not schedule a VERIFY_CONFLICT for C1.
        orch._apply_conflict_status(state, {"GOAL_1"}, next_actions=[])
        self.assertEqual(state.conflicts[0]["conflict_status"], "NON_BLOCKING")


class NormalizeEvidenceConflictUpdatesTests(unittest.TestCase):
    def test_new_conflict_starts_unclassified(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        response = {
            "evidences": [
                {
                    "evidence_id": "Ea",
                    "statement": "s",
                    "quotes": [{"web_id": "9", "quotes": ["t"]}],
                    "evidence_role": "r",
                    "evidence_type": "direct_answer",
                }
            ],
            "conflicts": [
                {"conflict_object": "x", "conflict_evidence_ids": ["Ea"]}
            ],
        }
        evidences, conflicts, updated_conflicts = orch._normalize_evidence(
            response, "GOAL_1", state, allowed_web_ids={"9"}
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["conflict_status"], "UNCLASSIFIED")
        self.assertEqual(updated_conflicts, [])

    def test_conflict_updates_merges_into_existing_conflict_without_new_id(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        state.evidences = [{"evidence_id": "E1", "search_goal_id": "GOAL_1"}]
        state.conflicts = [
            {
                "conflict_id": "C1",
                "search_goal_id": "GOAL_1",
                "conflict_object": "x",
                "conflict_status": "SOLVING",
                "conflict_evidence_ids": ["E1"],
            }
        ]
        response = {
            "evidences": [
                {
                    "evidence_id": "E_local",
                    "statement": "更权威的澄清",
                    "quotes": [{"web_id": "9", "quotes": ["t"]}],
                    "evidence_role": "r",
                    "evidence_type": "direct_answer",
                }
            ],
            "conflicts": [],
            "conflict_updates": [
                {"existing_conflict_id": "C1", "add_evidence_ids": ["E_local"]}
            ],
        }

        evidences, conflicts, updated_conflicts = orch._normalize_evidence(
            response, "GOAL_1", state, allowed_web_ids={"9"}
        )

        self.assertEqual(conflicts, [])  # no duplicate conflict minted
        self.assertEqual(len(updated_conflicts), 1)
        self.assertEqual(updated_conflicts[0]["conflict_id"], "C1")
        self.assertEqual(updated_conflicts[0]["conflict_evidence_ids"], ["E1", "E2"])
        # The merge mutates the entry already stored in state.conflicts in place.
        self.assertEqual(state.conflicts[0]["conflict_evidence_ids"], ["E1", "E2"])

    def test_conflict_updates_rejects_unknown_existing_conflict_id(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        response = {
            "evidences": [],
            "conflicts": [],
            "conflict_updates": [
                {"existing_conflict_id": "C99", "add_evidence_ids": []}
            ],
        }
        with self.assertRaises(ProtocolError):
            orch._normalize_evidence(
                response, "GOAL_1", state, allowed_web_ids=set()
            )

    def test_conflict_updates_rejects_conflict_from_a_different_goal(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        state.conflicts = [
            {
                "conflict_id": "C1",
                "search_goal_id": "GOAL_OTHER",
                "conflict_evidence_ids": [],
            }
        ]
        response = {
            "evidences": [],
            "conflicts": [],
            "conflict_updates": [
                {"existing_conflict_id": "C1", "add_evidence_ids": []}
            ],
        }
        with self.assertRaises(ProtocolError):
            orch._normalize_evidence(
                response, "GOAL_1", state, allowed_web_ids=set()
            )

    def test_conflict_updates_rejects_unknown_evidence_id(self):
        orch = _noop_orchestrator()
        state = SearchState(user_query="q", current_date="2026-08-13")
        state.conflicts = [
            {
                "conflict_id": "C1",
                "search_goal_id": "GOAL_1",
                "conflict_evidence_ids": [],
            }
        ]
        response = {
            "evidences": [],
            "conflicts": [],
            "conflict_updates": [
                {"existing_conflict_id": "C1", "add_evidence_ids": ["E999"]}
            ],
        }
        with self.assertRaises(ProtocolError):
            orch._normalize_evidence(
                response, "GOAL_1", state, allowed_web_ids=set()
            )


class ConflictDedupIntegrationTest(unittest.TestCase):
    """A 2-turn run: the same disagreement resurfaces in turn 2 and must
    extend the existing conflict rather than mint a near-duplicate one."""

    def test_recurring_disagreement_stays_one_conflict_across_turns(self):
        class FakeTeacher:
            def __init__(self):
                self.planner_calls = 0
                self.evidence_calls = 0

            def __call__(self, messages):
                system = messages[0]["content"]
                if "Search Planner" in system:
                    self.planner_calls += 1
                    if self.planner_calls == 1:
                        return {
                            "search_actions": [
                                {
                                    "search_action": "INIT_GOAL",
                                    "action_reason": "起始",
                                    "target_goal_id": None,
                                    "target_conflict_id": None,
                                    "search_goal_id": "GOAL_1",
                                    "search_goal": "测试目标",
                                    "depends_on_goal_ids": [],
                                    "search_queries": ["q1"],
                                }
                            ]
                        }
                    if self.planner_calls == 2:
                        return {
                            "search_state_updates": [
                                {
                                    "search_goal_id": "GOAL_1",
                                    "search_goal": "测试目标",
                                    "status": "OPEN",
                                    "status_reason": "存在阻塞冲突",
                                    "answer": "",
                                    "supported_statement": "",
                                    "supporting_evidence_ids": [],
                                    "conflict_summary": "分歧待核实",
                                    "conflicting_evidence_ids": ["E1", "E2"],
                                    "evidence_gap": "需核实哪个信源准确",
                                }
                            ],
                            "next_turn_search_actions": {
                                "action_reason": "核实冲突",
                                "search_actions": [
                                    {
                                        "search_action": "VERIFY_CONFLICT",
                                        "action_reason": "核实C1",
                                        "target_goal_id": "GOAL_1",
                                        "target_conflict_id": "C1",
                                        "search_queries": ["q2"],
                                    }
                                ],
                            },
                        }
                    return {
                        "search_state_updates": [
                            {
                                "search_goal_id": "GOAL_1",
                                "search_goal": "测试目标",
                                "status": "CLOSED",
                                "status_reason": "已核实",
                                "answer": "已确认",
                                "supported_statement": "已确认事实",
                                "supporting_evidence_ids": ["E1", "E2", "E3"],
                                "conflict_summary": "",
                                "conflicting_evidence_ids": [],
                                "evidence_gap": "",
                            }
                        ],
                        "next_turn_search_actions": {
                            "action_reason": "已闭环",
                            "search_actions": [
                                {"search_action": "STOP", "action_reason": "已闭环"}
                            ],
                        },
                    }

                self.evidence_calls += 1
                if self.evidence_calls == 1:
                    return {
                        "evidences": [
                            {
                                "evidence_id": "Ea",
                                "statement": "陈述A",
                                "quotes": [{"web_id": "1", "quotes": ["原文A"]}],
                                "evidence_role": "r",
                                "evidence_type": "direct_answer",
                            },
                            {
                                "evidence_id": "Eb",
                                "statement": "陈述B",
                                "quotes": [{"web_id": "2", "quotes": ["原文B"]}],
                                "evidence_role": "r",
                                "evidence_type": "counter_evidence",
                            },
                        ],
                        "conflicts": [
                            {
                                "conflict_object": "陈述A与陈述B矛盾",
                                "conflict_evidence_ids": ["Ea", "Eb"],
                            }
                        ],
                        "conflict_updates": [],
                    }
                # Turn 2's VERIFY_CONFLICT hits the same underlying dispute
                # again; it must extend C1, not mint a second conflict.
                return {
                    "evidences": [
                        {
                            "evidence_id": "Ec",
                            "statement": "陈述C，权威信源澄清",
                            "quotes": [{"web_id": "3", "quotes": ["原文C"]}],
                            "evidence_role": "r",
                            "evidence_type": "direct_answer",
                        }
                    ],
                    "conflicts": [],
                    "conflict_updates": [
                        {"existing_conflict_id": "C1", "add_evidence_ids": ["Ec"]}
                    ],
                }

        def fake_web_search(query, top_k=10):
            return [
                {
                    "url": f"https://example.com/{query}/{i}",
                    "title": f"{query}-{i}",
                    "date": "2026-08-01",
                    "content": f"{query} 原文 {i}",
                    "website": "测试站点",
                    "web_content": f"网页来源: 测试站点｜网页内容：{query} 原文 {i}",
                }
                for i in range(2)
            ][:top_k]

        orchestrator = DeepSearchOrchestrator(
            FakeTeacher(), fake_web_search, config=OrchestratorConfig(max_turns=3)
        )
        state = orchestrator.run("测试 query", current_date="2026-08-13")

        self.assertEqual(state.run_status, "COMPLETED")
        self.assertEqual(len(state.conflicts), 1)
        self.assertEqual(state.conflicts[0]["conflict_id"], "C1")
        self.assertEqual(
            state.conflicts[0]["conflict_evidence_ids"], ["E1", "E2", "E3"]
        )
        # Goal closed in turn 2 without scheduling another VERIFY_CONFLICT.
        self.assertEqual(state.conflicts[0]["conflict_status"], "NON_BLOCKING")

        # Turn 1 really did choose to actively chase this exact conflict.
        turn1_next_actions = state.turns[0]["next_search_actions"]
        self.assertEqual(turn1_next_actions[0]["search_action"], "VERIFY_CONFLICT")
        self.assertEqual(turn1_next_actions[0]["target_conflict_id"], "C1")

        # Turn 2's execution surfaced a merge, not a brand-new conflict.
        turn2_execution = state.turns[1]["executions"][0]
        self.assertEqual(turn2_execution["conflicts"], [])
        self.assertEqual(len(turn2_execution["updated_conflicts"]), 1)
        self.assertEqual(
            turn2_execution["updated_conflicts"][0]["conflict_id"], "C1"
        )


if __name__ == "__main__":
    unittest.main()
