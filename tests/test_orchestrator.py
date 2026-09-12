from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deep_search.orchestrator import (
    DeepSearchOrchestrator,
    OrchestratorConfig,
    ProtocolError,
)
from deep_search.models import GoalState, SearchState


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
                            "action_reason": "先确认基础事实",
                            "target_goal_id": None,
                            "target_conflict_id": None,
                            "search_goal_id": "GOAL_1",
                            "search_goal": "确认测试事实",
                            "depends_on_goal_ids": [],
                            "search_queries": ["测试事实 官方"],
                        }
                    ]
                }
            return {
                "search_state_updates": [
                    {
                        "search_goal_id": "GOAL_1",
                        "search_goal": "确认测试事实",
                        "status": "CLOSED",
                        "status_reason": "已有直接证据",
                        "answer": "测试事实成立",
                        "supported_statement": "测试事实于2026年发生",
                        "supporting_evidence_ids": ["E1"],
                        "conflict_summary": "",
                        "conflicting_evidence_ids": [],
                        "evidence_gap": "",
                        "depends_on_goal_ids": [],
                    }
                ],
                "next_turn_search_actions": {
                    "action_reason": "必要目标已经闭环",
                    "search_actions": [
                        {
                            "search_action": "STOP",
                            "action_reason": "必要目标已经闭环",
                        }
                    ],
                },
            }

        self.evidence_calls += 1
        return {
            "evidences": [
                {
                    "evidence_id": "E99",
                    "statement": "测试事实于2026年发生",
                    "quotes": [
                        {"web_id": "网页[1]", "quotes": ["测试事实于2026年发生。"]}
                    ],
                    "evidence_role": "直接回答",
                    "evidence_type": "direct_answer",
                }
            ],
            "conflicts": [],
        }


def fake_web_search(query, top_k=10):
    return [
        {
            "id": 7,
            "url": "https://example.com/fact",
            "title": "测试事实",
            "date": "2026-01-01",
            "content": "测试事实于2026年发生。",
            "type": "web",
            "website": "示例官网",
            "rerank_score": 1,
            "authority_score": 0.9,
            "authority_level": "高",
            "web_content": "网页来源: 示例官网",
        }
    ][:top_k]


class OrchestratorTests(unittest.TestCase):
    def test_complete_one_turn_and_persist_handoff(self):
        teacher = FakeTeacher()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trajectory.json"
            orchestrator = DeepSearchOrchestrator(
                teacher,
                fake_web_search,
                config=OrchestratorConfig(max_turns=3, top_k=5),
                output_path=output,
            )
            state = orchestrator.run("测试 query", current_date="2026-08-09")

            self.assertEqual(state.run_status, "COMPLETED")
            self.assertEqual(state.metrics.scheme, "scheme_a")
            self.assertEqual(state.metrics.total_turns, 1)
            self.assertEqual(state.metrics.search_turns, 1)
            self.assertEqual(state.metrics.model_call_count, 3)
            self.assertEqual(state.metrics.search_action_count, 1)
            self.assertEqual(state.metrics.search_query_count, 1)
            self.assertEqual(state.metrics.web_result_count, 1)
            self.assertEqual(state.metrics.unique_web_count, 1)
            self.assertEqual(state.metrics.termination_type, "MODEL_STOP")
            self.assertEqual(state.metrics.final_action, "STOP")
            self.assertEqual(state.goals["GOAL_1"].status, "CLOSED")
            self.assertEqual(state.evidences[0]["evidence_id"], "E1")
            self.assertEqual(
                state.evidences[0]["quotes"][0]["web_id"], "1"
            )
            self.assertEqual(teacher.planner_calls, 2)
            self.assertEqual(teacher.evidence_calls, 1)
            self.assertEqual(len(state.turns), 1)

            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("trajectory", saved)
            self.assertIn("answer_agent_handoff", saved)
            self.assertEqual(
                saved["answer_agent_handoff"]["search_goals"][0]["status"],
                "CLOSED",
            )
            self.assertEqual(
                saved["trajectory"]["metrics"]["termination_type"],
                "MODEL_STOP",
            )
            saved_turn = saved["trajectory"]["turns"][0]
            self.assertEqual(len(saved_turn["planner_input"]["messages"]), 2)
            self.assertEqual(
                len(
                    saved_turn["executions"][0]["evidence_processor"][
                        "messages"
                    ]
                ),
                2,
            )
            self.assertEqual(len(saved_turn["planner_output"]["messages"]), 2)
            self.assertNotIn(
                "depends_on_goal_ids",
                saved_turn["state_updates"][0],
            )

    def test_initial_action_must_be_init_goal(self):
        def invalid_teacher(_messages):
            return {
                "search_actions": [
                    {"search_action": "STOP", "action_reason": "错误停止"}
                ]
            }

        orchestrator = DeepSearchOrchestrator(invalid_teacher, fake_web_search)
        with self.assertRaisesRegex(ProtocolError, "INIT_GOAL"):
            orchestrator.run("测试")

    def test_evidence_cannot_cite_a_web_outside_current_turn(self):
        class InvalidCitationTeacher(FakeTeacher):
            def __call__(self, messages):
                response = super().__call__(messages)
                if "Evidence Processor" in messages[0]["content"]:
                    response["evidences"][0]["quotes"][0]["web_id"] = "999"
                return response

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed-trajectory.json"
            orchestrator = DeepSearchOrchestrator(
                InvalidCitationTeacher(), fake_web_search, output_path=output
            )
            with self.assertRaisesRegex(ProtocolError, "outside current_search_webs"):
                orchestrator.run("测试")
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["trajectory"]["run_status"], "FAILED")
            self.assertEqual(
                saved["trajectory"]["errors"][0]["stage"],
                "evidence_processor",
            )
            self.assertEqual(
                saved["trajectory"]["errors"][0]["last_model_call"]["agent"],
                "evidence_processor",
            )
            self.assertEqual(
                len(
                    saved["trajectory"]["errors"][0]["last_model_call"][
                        "messages"
                    ]
                ),
                2,
            )

    def test_legacy_supported_evidence_alias_wins_over_empty_new_field(self):
        class LegacyFieldTeacher(FakeTeacher):
            def __call__(self, messages):
                response = super().__call__(messages)
                if (
                    "Search Planner" in messages[0]["content"]
                    and self.planner_calls == 2
                ):
                    update = response["search_state_updates"][0]
                    update["supporting_evidence_ids"] = []
                    update["supported_evidence_ids"] = ["E1"]
                return response

        state = DeepSearchOrchestrator(
            LegacyFieldTeacher(), fake_web_search
        ).run("测试")
        self.assertEqual(
            state.goals["GOAL_1"].supporting_evidence_ids,
            ["E1"],
        )

    def test_scheme_a_max_turns_metrics(self):
        class ContinueTeacher(FakeTeacher):
            def __call__(self, messages):
                response = super().__call__(messages)
                if (
                    "Search Planner" in messages[0]["content"]
                    and self.planner_calls == 2
                ):
                    update = response["search_state_updates"][0]
                    update["status"] = "OPEN"
                    update["status_reason"] = "仍需补充"
                    update["evidence_gap"] = "缺少第二来源"
                    response["next_turn_search_actions"] = {
                        "action_reason": "继续搜索",
                        "search_actions": [
                            {
                                "search_action": "CONTINUE_SEARCH",
                                "action_reason": "继续搜索",
                                "target_goal_id": "GOAL_1",
                                "target_conflict_id": None,
                                "search_focus": "补充第二来源",
                                "search_queries": ["测试事实 第二来源"],
                            }
                        ],
                    }
                return response

        state = DeepSearchOrchestrator(
            ContinueTeacher(),
            fake_web_search,
            config=OrchestratorConfig(max_turns=1),
        ).run("测试")

        self.assertEqual(state.run_status, "MAX_TURNS_REACHED")
        self.assertEqual(state.metrics.total_turns, 1)
        self.assertEqual(state.metrics.search_turns, 1)
        self.assertEqual(state.metrics.termination_type, "MAX_TURNS")
        self.assertEqual(state.metrics.final_action, "MAX_TURNS_REACHED")
        self.assertEqual(state.metrics.last_model_action, "SEARCH")

    def test_conflicting_evidence_ids_accept_evidence_namespace(self):
        state = SearchState(user_query="测试", current_date="2026-08-09")
        state.goals["GOAL_1"] = GoalState("GOAL_1", "测试冲突")
        state.evidences = [{"evidence_id": "E4"}, {"evidence_id": "E5"}]
        update = {
            "search_goal_id": "GOAL_1",
            "status": "OPEN",
            "supporting_evidence_ids": ["E4"],
            "conflicting_evidence_ids": ["E4", "E5"],
        }

        DeepSearchOrchestrator(FakeTeacher(), fake_web_search)._apply_state_updates(
            [update], state
        )

        self.assertEqual(
            state.goals["GOAL_1"].conflicting_evidence_ids,
            ["E4", "E5"],
        )

    def test_state_update_cannot_modify_or_validate_goal_dependencies(self):
        state = SearchState(user_query="测试", current_date="2026-08-09")
        state.goals["GOAL_PARENT"] = GoalState("GOAL_PARENT", "父目标")
        state.goals["GOAL_1"] = GoalState(
            "GOAL_1",
            "子目标",
            depends_on_goal_ids=["GOAL_PARENT"],
        )
        state.evidences = [{"evidence_id": "E1"}]
        update = {
            "search_goal_id": "GOAL_1",
            "status": "OPEN",
            "supporting_evidence_ids": ["E1"],
            "conflicting_evidence_ids": [],
            # Even an unknown ID from a stale model schema must be ignored.
            "depends_on_goal_ids": ["GOAL_UNKNOWN"],
        }

        DeepSearchOrchestrator(FakeTeacher(), fake_web_search)._apply_state_updates(
            [update], state
        )

        self.assertEqual(
            state.goals["GOAL_1"].depends_on_goal_ids,
            ["GOAL_PARENT"],
        )
        self.assertNotIn("depends_on_goal_ids", update)

    def test_legacy_conflict_id_is_expanded_to_evidence_ids(self):
        state = SearchState(user_query="测试", current_date="2026-08-09")
        state.goals["GOAL_1"] = GoalState("GOAL_1", "测试冲突")
        state.evidences = [{"evidence_id": "E7"}, {"evidence_id": "E8"}]
        state.conflicts = [
            {
                "conflict_id": "C1",
                "conflict_evidence_ids": ["E7", "E8"],
                "search_goal_id": "GOAL_1",
            }
        ]
        update = {
            "search_goal_id": "GOAL_1",
            "status": "OPEN",
            "supporting_evidence_ids": ["E7"],
            "conflicting_evidence_ids": ["C1"],
        }

        DeepSearchOrchestrator(FakeTeacher(), fake_web_search)._apply_state_updates(
            [update], state
        )

        self.assertEqual(update["conflicting_evidence_ids"], ["E7", "E8"])
        self.assertEqual(
            state.goals["GOAL_1"].conflicting_evidence_ids,
            ["E7", "E8"],
        )

    def test_repeated_closed_goal_preserves_existing_support(self):
        state = SearchState(user_query="测试", current_date="2026-08-09")
        state.goals["GOAL_1"] = GoalState(
            "GOAL_1",
            "已经闭环的目标",
            status="CLOSED",
            supporting_evidence_ids=["E1"],
        )
        state.evidences = [{"evidence_id": "E1"}]
        update = {
            "search_goal_id": "GOAL_1",
            "status": "CLOSED",
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
        }

        DeepSearchOrchestrator(FakeTeacher(), fake_web_search)._apply_state_updates(
            [update], state
        )

        self.assertEqual(update["supporting_evidence_ids"], ["E1"])
        self.assertEqual(
            state.goals["GOAL_1"].supporting_evidence_ids,
            ["E1"],
        )

    def test_empty_model_content_error_is_retried(self):
        class RetryTeacher(FakeTeacher):
            def __init__(self):
                super().__init__()
                self.total_calls = 0

            def __call__(self, messages):
                self.total_calls += 1
                if self.total_calls == 1:
                    raise ValueError("model returned empty content")
                return super().__call__(messages)

        teacher = RetryTeacher()
        state = DeepSearchOrchestrator(
            teacher,
            fake_web_search,
            config=OrchestratorConfig(
                model_max_attempts=2,
                model_retry_delay_seconds=0,
            ),
        ).run("测试")

        self.assertEqual(state.run_status, "COMPLETED")
        self.assertEqual(state.metrics.model_call_count, 4)
        self.assertEqual(teacher.total_calls, 4)

    def test_exhausted_model_retries_persist_current_error(self):
        def empty_teacher(_messages):
            raise ValueError("model returned empty content")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed-empty-model.json"
            orchestrator = DeepSearchOrchestrator(
                empty_teacher,
                fake_web_search,
                config=OrchestratorConfig(
                    model_max_attempts=2,
                    model_retry_delay_seconds=0,
                ),
                output_path=output,
            )
            with self.assertRaisesRegex(ValueError, "empty content"):
                orchestrator.run("测试")

            saved = json.loads(output.read_text(encoding="utf-8"))
            last_call = saved["trajectory"]["errors"][-1]["last_model_call"]
            self.assertEqual(last_call["attempt"], 2)
            self.assertEqual(last_call["error_type"], "ValueError")
            self.assertEqual(len(last_call["messages"]), 2)
            self.assertEqual(len(last_call["attempts"]), 2)
            self.assertTrue(
                all("messages" not in attempt for attempt in last_call["attempts"])
            )
            self.assertEqual(
                json.dumps(last_call, ensure_ascii=False).count('"messages"'),
                1,
            )
            self.assertEqual(
                saved["trajectory"]["metrics"]["model_call_count"],
                2,
            )


if __name__ == "__main__":
    unittest.main()
