"""Tests for the reworked prompt assembly in deep_search_claude/context.py.

Mirrors tests/test_context.py's style but targets the pieces that were
intentionally redesigned: search_history (decision-trace replay, no raw web
content, no quotes), current_turn_info (web-major, deduped, full web content
kept, evidence quotes dropped), and evidence_messages's added
<prior_conflicts> block (feeds the conflict-dedup feature in
orchestrator.py). planner_initial_messages is otherwise unchanged from
deep_search/context.py, so it gets a light parity check rather than full
re-coverage.
"""

import json
import unittest

from deep_search_claude.context import ContextAssembler
from deep_search_claude.models import GoalState, SearchState


class ClaudeContextAssemblerTests(unittest.TestCase):
    def setUp(self):
        self.state = SearchState(user_query="测试", current_date="2026-08-09")
        self.assembler = ContextAssembler()

    # ------------------------------------------------------------------
    # search_history: decision trace, no raw content, no quotes
    # ------------------------------------------------------------------

    def test_search_history_replays_decisions_without_raw_content_or_quotes(self):
        self.state.goals["GOAL_1"] = GoalState(
            search_goal_id="GOAL_1",
            search_goal="验证网页上下文",
            status="OPEN",
        )
        historical_web = {
            "web_id": "1",
            "url": "https://must-not-appear.example",
            "content": "raw-content-must-not-appear",
            "web_content": "网页来源: 示例｜网页内容：正文不应出现在 search_history 中",
        }
        self.state.turns.append(
            {
                "turn_id": 1,
                "search_actions": [
                    {
                        "search_action": "INIT_GOAL",
                        "action_reason": "先验证历史事实",
                        "search_goal_id": "GOAL_1",
                        "search_queries": ["历史 query 1", "历史 query 2"],
                    }
                ],
                "executions": [
                    {
                        "search_action": {
                            "search_action": "INIT_GOAL",
                            "action_reason": "先验证历史事实",
                        },
                        "search_goal_id": "GOAL_1",
                        "search_queries": ["历史 query 1", "历史 query 2"],
                        "search_errors": [],
                        "webs": [historical_web],
                        "evidences": [
                            {
                                "evidence_id": "E1",
                                "search_goal_id": "GOAL_1",
                                "statement": "历史证据陈述",
                                "quotes": [
                                    {
                                        "web_id": "1",
                                        "quotes": ["原文引用不应出现在 search_history 中"],
                                    }
                                ],
                            }
                        ],
                        "conflicts": [],
                    }
                ],
                "state_updates": [
                    {
                        "search_goal_id": "GOAL_1",
                        "status": "OPEN",
                        "status_reason": "仍有缺口",
                        "evidence_gap": "缺少后续证据",
                        "conflict_summary": "",
                    }
                ],
                "next_search_actions": [
                    {
                        "search_action": "CONTINUE_SEARCH",
                        "action_reason": "继续挖掘缺口",
                        "target_goal_id": "GOAL_1",
                        "search_queries": ["历史 query 3"],
                    }
                ],
            }
        )
        current_execution = {
            "search_goal_id": "GOAL_1",
            "search_queries": ["当前 query"],
            "search_errors": [],
            "webs": [],
            "evidences": [],
            "conflicts": [],
        }

        prompt = self.assembler.planner_update_messages(
            self.state, [current_execution]
        )[1]["content"]
        history = prompt.split("<search_history>\n", 1)[1].split(
            "\n</search_history>", 1
        )[0]

        self.assertLess(
            prompt.index("</constant_info>"), prompt.index("<search_history>")
        )
        self.assertLess(
            prompt.index("</search_history>"), prompt.index("<search_status>")
        )

        # Decision trace fields are present.
        self.assertIn("<turn1>", history)
        self.assertIn("search_goal_id: GOAL_1", history)
        self.assertIn("search_goal: 验证网页上下文", history)
        self.assertIn("search_motivation: 先验证历史事实", history)
        self.assertIn("search_queries:\n- 历史 query 1\n- 历史 query 2", history)
        self.assertIn("evidence_found:\n- E1: 历史证据陈述", history)
        self.assertIn("state_after_turn:", history)
        self.assertIn("status: OPEN", history)
        self.assertIn("evidence_gap: 缺少后续证据", history)
        self.assertIn("next_action_planned: CONTINUE_SEARCH — 继续挖掘缺口", history)
        self.assertIn("</turn1>", history)

        # Raw web content and quotes must not leak into the history replay.
        self.assertNotIn("正文不应出现在 search_history 中", history)
        self.assertNotIn("原文引用不应出现在 search_history 中", history)
        self.assertNotIn(historical_web["url"], history)
        self.assertNotIn(historical_web["content"], history)

    def test_search_history_marks_refine_goal_replacement(self):
        self.state.goals["GOAL_1"] = GoalState(
            search_goal_id="GOAL_1", search_goal="旧目标", status="SUPERSEDED"
        )
        self.state.turns.append(
            {
                "turn_id": 1,
                "search_actions": [
                    {
                        "search_action": "INIT_GOAL",
                        "action_reason": "起始",
                        "search_goal_id": "GOAL_1",
                        "search_queries": ["q"],
                    }
                ],
                "executions": [
                    {
                        "search_action": {
                            "search_action": "INIT_GOAL",
                            "action_reason": "起始",
                        },
                        "search_goal_id": "GOAL_1",
                        "search_queries": ["q"],
                        "search_errors": [],
                        "webs": [],
                        "evidences": [],
                        "conflicts": [],
                    }
                ],
                "state_updates": [
                    {
                        "search_goal_id": "GOAL_1",
                        "status": "SUPERSEDED",
                        "status_reason": "目标定义不够精确",
                        "evidence_gap": "",
                        "conflict_summary": "",
                    }
                ],
                "next_search_actions": [
                    {
                        "search_action": "REFINE_GOAL",
                        "action_reason": "缩小范围",
                        "target_goal_id": "GOAL_1",
                        "search_goal_id": "GOAL_2",
                        "search_goal": "更精确的新目标",
                        "search_queries": ["q2"],
                    }
                ],
            }
        )
        current_execution = {
            "search_goal_id": "GOAL_2",
            "search_queries": ["q3"],
            "search_errors": [],
            "webs": [],
            "evidences": [],
            "conflicts": [],
        }
        history = self.assembler.planner_update_messages(
            self.state, [current_execution]
        )[1]["content"].split("<search_history>\n", 1)[1].split(
            "\n</search_history>", 1
        )[0]

        self.assertIn(
            "next_action_planned: REFINE_GOAL — 被替换为 GOAL_2（缩小范围）", history
        )

    # ------------------------------------------------------------------
    # current_turn_info: web-major, deduped, full content, no quotes
    # ------------------------------------------------------------------

    def test_current_turn_info_dedupes_shared_web_and_drops_quotes(self):
        self.state.goals["GOAL_A"] = GoalState(
            search_goal_id="GOAL_A", search_goal="目标 A"
        )
        self.state.goals["GOAL_B"] = GoalState(
            search_goal_id="GOAL_B", search_goal="目标 B"
        )
        web_shared = {
            "web_id": "50",
            "url": "https://shared.example",
            "content": "raw-shared-must-not-appear",
            "web_content": "网页来源: 共享站点｜网页内容：共享网页完整原文ABC",
        }
        web_a_only = {
            "web_id": "51",
            "web_content": "网页来源: A站点｜网页内容：A专属原文",
        }
        web_c_unused = {
            "web_id": "53",
            "web_content": "网页来源: C站点｜网页内容：C专属原文，未被证据引用",
        }
        web_b_only = {
            "web_id": "52",
            "web_content": "网页来源: B站点｜网页内容：B专属原文",
        }

        execution_a = {
            "search_goal_id": "GOAL_A",
            "search_queries": ["query a"],
            "search_errors": [],
            "webs": [web_shared, web_a_only, web_c_unused],
            "evidences": [
                {
                    "evidence_id": "E10",
                    "search_goal_id": "GOAL_A",
                    "statement": "A的结论陈述",
                    "quotes": [{"web_id": "50", "quotes": ["quote text A"]}],
                }
            ],
            "conflicts": [],
        }
        execution_b = {
            "search_goal_id": "GOAL_B",
            "search_queries": ["query b"],
            "search_errors": [],
            "webs": [web_shared, web_b_only],
            "evidences": [
                {
                    "evidence_id": "E11",
                    "search_goal_id": "GOAL_B",
                    "statement": "B的结论陈述",
                    "quotes": [
                        {"web_id": "50", "quotes": ["quote text B"]},
                        {"web_id": "52", "quotes": ["quote text B2"]},
                    ],
                }
            ],
            "conflicts": [
                {
                    "conflict_id": "C5",
                    "search_goal_id": "GOAL_B",
                    "conflict_object": "冲突主体",
                    "conflict_status": "UNCLASSIFIED",
                    "conflict_evidence_ids": ["E10", "E11"],
                }
            ],
        }

        current_turn_info = self.assembler._current_turn_info_text(
            self.state, [execution_a, execution_b]
        )

        # Shared web's full body appears exactly once even though two goals
        # cite it; "[50]" additionally shows up once more inside the "见
        # [50]" back-reference for E11, which is the point of this test.
        self.assertEqual(current_turn_info.count("[50]网页来源"), 1)
        self.assertEqual(current_turn_info.count("共享网页完整原文ABC"), 1)
        self.assertIn("A专属原文", current_turn_info)
        self.assertIn("B专属原文", current_turn_info)
        self.assertIn("C专属原文，未被证据引用", current_turn_info)

        # Evidence attached with statement, tagged by goal, no quotes leak through.
        self.assertIn("evidence[GOAL_A/E10]: A的结论陈述", current_turn_info)
        self.assertEqual(
            current_turn_info.count("evidence[GOAL_B/E11]: B的结论陈述"), 1
        )
        self.assertIn("同 E11（见 [50]）", current_turn_info)
        for leaked_quote in ("quote text A", "quote text B", "quote text B2"):
            self.assertNotIn(leaked_quote, current_turn_info)
        self.assertNotIn(web_shared["url"], current_turn_info)
        self.assertNotIn(web_shared["content"], current_turn_info)

        # A web with no supporting evidence keeps its full body but no evidence line.
        unused_block = current_turn_info.split("[53]", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("evidence[", unused_block)

        # Goal index header surfaces per-goal new evidence ids.
        self.assertIn("search_goal_id: GOAL_A", current_turn_info)
        self.assertIn("new_evidence_ids: E10", current_turn_info)
        self.assertIn("search_goal_id: GOAL_B", current_turn_info)
        self.assertIn("new_evidence_ids: E11", current_turn_info)

        # Conflicts are listed with their evidence linkage, kind, and status.
        self.assertIn("conflict_id: C5", current_turn_info)
        self.assertIn("kind: 新冲突", current_turn_info)
        self.assertIn("search_goal_id: GOAL_B", current_turn_info)
        self.assertIn("conflict_status: UNCLASSIFIED", current_turn_info)
        self.assertIn("conflict_object: 冲突主体", current_turn_info)
        self.assertIn("conflict_evidence_ids: E10, E11", current_turn_info)

    def test_current_turn_info_marks_updated_conflicts_distinctly_from_new(self):
        execution = {
            "search_goal_id": "GOAL_A",
            "search_queries": ["q"],
            "search_errors": [],
            "webs": [],
            "evidences": [],
            "conflicts": [],
            "updated_conflicts": [
                {
                    "conflict_id": "C1",
                    "search_goal_id": "GOAL_A",
                    "conflict_object": "旧冲突主体",
                    "conflict_status": "SOLVING",
                    "conflict_evidence_ids": ["E1", "E2", "E5"],
                }
            ],
        }
        current_turn_info = self.assembler._current_turn_info_text(
            self.state, [execution]
        )
        self.assertIn("conflict_id: C1", current_turn_info)
        self.assertIn("kind: 已有冲突补充证据（非新冲突）", current_turn_info)
        self.assertIn("conflict_status: SOLVING", current_turn_info)
        self.assertIn("conflict_evidence_ids: E1, E2, E5", current_turn_info)
        # An updated conflict is not also treated as a brand-new one.
        self.assertNotIn("（本轮无冲突）", current_turn_info)

    def test_search_history_conflict_found_distinguishes_new_from_updated(self):
        self.state.goals["GOAL_1"] = GoalState(
            search_goal_id="GOAL_1", search_goal="目标"
        )
        self.state.turns.append(
            {
                "turn_id": 1,
                "search_actions": [
                    {
                        "search_action": "CONTINUE_SEARCH",
                        "action_reason": "继续核实",
                        "target_goal_id": "GOAL_1",
                        "search_queries": ["q"],
                    }
                ],
                "executions": [
                    {
                        "search_action": {
                            "search_action": "CONTINUE_SEARCH",
                            "action_reason": "继续核实",
                        },
                        "search_goal_id": "GOAL_1",
                        "search_queries": ["q"],
                        "search_errors": [],
                        "webs": [],
                        "evidences": [],
                        "conflicts": [],
                        "updated_conflicts": [
                            {
                                "conflict_id": "C1",
                                "search_goal_id": "GOAL_1",
                                "conflict_object": "旧冲突",
                                "conflict_status": "SOLVING",
                                "conflict_evidence_ids": ["E1", "E2", "E5"],
                            }
                        ],
                    }
                ],
                "state_updates": [
                    {
                        "search_goal_id": "GOAL_1",
                        "status": "OPEN",
                        "status_reason": "仍在核实",
                        "evidence_gap": "",
                        "conflict_summary": "",
                    }
                ],
                "next_search_actions": [],
            }
        )
        current_execution = {
            "search_goal_id": "GOAL_1",
            "search_queries": ["q2"],
            "search_errors": [],
            "webs": [],
            "evidences": [],
            "conflicts": [],
        }
        history = self.assembler.planner_update_messages(
            self.state, [current_execution]
        )[1]["content"].split("<search_history>\n", 1)[1].split(
            "\n</search_history>", 1
        )[0]
        self.assertIn("C1（补充证据，非新冲突）: 当前涉及 E1, E2, E5", history)

    def test_current_turn_info_empty_sections_have_placeholders(self):
        execution = {
            "search_goal_id": "GOAL_A",
            "search_queries": ["q"],
            "search_errors": [],
            "webs": [],
            "evidences": [],
            "conflicts": [],
        }
        current_turn_info = self.assembler._current_turn_info_text(
            self.state, [execution]
        )
        self.assertIn("（本轮没有检索到网页）", current_turn_info)
        self.assertIn("（本轮无冲突）", current_turn_info)
        self.assertIn("new_evidence_ids: （无）", current_turn_info)

    # ------------------------------------------------------------------
    # Parity checks: unchanged pieces still behave like deep_search/context.py
    # ------------------------------------------------------------------

    def test_evidence_prompt_uses_only_id_and_web_content(self):
        self.state.goals["GOAL_1"] = GoalState(
            search_goal_id="GOAL_1", search_goal="验证网页上下文"
        )
        web = {
            "web_id": "1",
            "url": "https://must-not-appear.example",
            "content": "raw-content-must-not-appear",
            "web_content": "网页来源: 示例｜网页内容：正文",
        }
        prompt = self.assembler.evidence_messages(self.state, "GOAL_1", [web])[1][
            "content"
        ]
        self.assertIn("[1]" + web["web_content"], prompt)
        self.assertNotIn(web["url"], prompt)
        self.assertNotIn(web["content"], prompt)

    def test_evidence_prompt_exposes_prior_conflicts_for_this_goal_only(self):
        self.state.goals["GOAL_1"] = GoalState(
            search_goal_id="GOAL_1", search_goal="验证网页上下文"
        )
        self.state.conflicts = [
            {
                "conflict_id": "C1",
                "search_goal_id": "GOAL_1",
                "conflict_object": "发布部门数量与生效日期矛盾",
                "conflict_status": "NON_BLOCKING",
                "conflict_evidence_ids": ["E1", "E2"],
            },
            {
                "conflict_id": "C2",
                "search_goal_id": "GOAL_OTHER",
                "conflict_object": "与本目标无关的冲突",
                "conflict_status": "SOLVING",
                "conflict_evidence_ids": ["E9"],
            },
        ]
        prompt = self.assembler.evidence_messages(self.state, "GOAL_1", [])[1][
            "content"
        ]
        prior_conflicts_text = prompt.split("<prior_conflicts>\n", 1)[1].split(
            "\n</prior_conflicts>", 1
        )[0]
        self.assertIn('"conflict_id": "C1"', prior_conflicts_text)
        self.assertIn('"conflict_status": "NON_BLOCKING"', prior_conflicts_text)
        self.assertIn('"conflict_object": "发布部门数量与生效日期矛盾"', prior_conflicts_text)
        # A conflict belonging to a different goal must not leak through.
        self.assertNotIn("C2", prior_conflicts_text)
        self.assertNotIn("与本目标无关的冲突", prior_conflicts_text)

    def test_planner_prompts_are_phase_specific_and_examples_are_valid_json(self):
        initial_system = self.assembler.planner_initial_messages(
            "测试", "2026-08-10"
        )[0]["content"]
        update_system = self.assembler.planner_update_messages(
            self.state,
            [
                {
                    "search_goal_id": "GOAL_1",
                    "search_queries": ["query"],
                    "search_errors": [],
                    "webs": [],
                    "evidences": [],
                    "conflicts": [],
                }
            ],
        )[0]["content"]

        self.assertNotEqual(initial_system, update_system)
        self.assertIn("首轮唯一输出格式", initial_system)
        self.assertIn("信息未闭环时的输出格式", update_system)
        self.assertIn("信息已经闭环时的输出格式", update_system)

        open_example = update_system.split(
            "## 信息未闭环时的输出格式\n", 1
        )[1].split("\n\n## 信息已经闭环时的输出格式\n", 1)[0]
        closed_example = update_system.split(
            "## 信息已经闭环时的输出格式\n", 1
        )[1]
        self.assertEqual(
            json.loads(open_example)["next_turn_search_actions"]["search_actions"][
                0
            ]["search_action"],
            "CONTINUE_SEARCH",
        )
        self.assertEqual(
            json.loads(closed_example)["next_turn_search_actions"][
                "search_actions"
            ][0]["search_action"],
            "STOP",
        )


if __name__ == "__main__":
    unittest.main()
