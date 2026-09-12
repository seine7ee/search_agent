import json
import unittest

from deep_search.context import ContextAssembler
from deep_search.models import GoalState, SearchState


class ContextAssemblerTests(unittest.TestCase):
    def setUp(self):
        self.state = SearchState(user_query="测试", current_date="2026-08-09")
        self.state.goals["GOAL_1"] = GoalState(
            search_goal_id="GOAL_1",
            search_goal="验证网页上下文",
            status="CLOSED",
            answer="历史答案",
            conflict_summary="历史冲突",
            evidence_gap="历史缺口",
            supporting_evidence_ids=["E1"],
            conflicting_evidence_ids=["E2"],
            depends_on_goal_ids=["GOAL_PARENT"],
            evidence_ids=["E1", "E2"],
            conflict_ids=["C1"],
        )
        self.web = {
            "web_id": "1",
            "url": "https://must-not-appear.example",
            "title": "不得单独填充的标题",
            "content": "raw-content-must-not-appear",
            "website": "不得单独填充的网站",
            "date": "2026-01-01",
            "authority_level": "高",
            "web_content": "网页来源: 示例｜网页时间: 2026｜网页标题: 标题｜网页内容：正文",
        }
        self.assembler = ContextAssembler()

    def test_evidence_prompt_uses_only_id_and_web_content(self):
        messages = self.assembler.evidence_messages(
            self.state, "GOAL_1", [self.web]
        )
        prompt = messages[1]["content"]

        self.assertIn("[1]" + self.web["web_content"], prompt)
        self.assertNotIn(self.web["url"], prompt)
        self.assertNotIn(self.web["content"], prompt)

    def test_planner_prompt_uses_only_id_and_web_content(self):
        self.state.turns.append(
            {"state_updates": [{"search_goal_id": "GOAL_1"}]}
        )
        execution = {
            "search_goal_id": "GOAL_1",
            "search_queries": ["query"],
            "search_errors": [],
            "webs": [self.web],
            "evidences": [],
            "conflicts": [],
        }
        messages = self.assembler.planner_update_messages(self.state, [execution])
        prompt = messages[1]["content"]

        self.assertIn("[1]" + self.web["web_content"], prompt)
        self.assertNotIn(self.web["url"], prompt)
        self.assertNotIn(self.web["content"], prompt)
        self.assertIn("只能填写上下文中存在的 E 开头 evidence_id", prompt)
        search_status_text = prompt.split("<search_status>", 1)[1].split(
            "</search_status>", 1
        )[0]
        search_status = json.loads(search_status_text)
        self.assertEqual(
            search_status,
            [
                {
                    "search_goal_id": "GOAL_1",
                    "search_goal": "验证网页上下文",
                    "status": "CLOSED",
                    "answer": "历史答案",
                    "conflict_summary": "历史冲突",
                    "evidence_gap": "历史缺口",
                }
            ],
        )
        for forbidden_field in (
            "supported_statement",
            "supporting_evidence_ids",
            "conflicting_evidence_ids",
            "evidence_ids",
            "conflict_ids",
            "depends_on_goal_ids",
        ):
            self.assertNotIn(forbidden_field, search_status_text)
        self.assertIn(
            "search_state_updates 不得输出或修改该字段",
            messages[0]["content"],
        )

    def test_unjudged_goal_has_only_in_progress_compact_status(self):
        execution = {
            "search_goal_id": "GOAL_1",
            "search_queries": ["query"],
            "search_errors": [],
            "webs": [],
            "evidences": [],
            "conflicts": [],
        }
        prompt = self.assembler.planner_update_messages(
            self.state, [execution]
        )[1]["content"]
        search_status_text = prompt.split("<search_status>", 1)[1].split(
            "</search_status>", 1
        )[0]

        self.assertEqual(
            json.loads(search_status_text),
            [
                {
                    "search_goal_id": "GOAL_1",
                    "search_goal": "验证网页上下文",
                    "in_progress": True,
                }
            ],
        )
        self.assertNotIn('"status"', search_status_text)
        self.assertNotIn('"answer"', search_status_text)

    def test_planner_prompt_includes_search_history_before_status(self):
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
                        "webs": [self.web],
                    }
                ],
                "state_updates": [{"search_goal_id": "GOAL_1"}],
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

        self.assertLess(prompt.index("</constant_info>"), prompt.index("<search_history>"))
        self.assertLess(prompt.index("</search_history>"), prompt.index("<search_status>"))
        self.assertIn("<turn1>", history)
        self.assertIn("search_goal_id: GOAL_1", history)
        self.assertIn("search_goal: 验证网页上下文", history)
        self.assertIn("search_motivation: 先验证历史事实", history)
        self.assertIn("search_queries:\n- 历史 query 1\n- 历史 query 2", history)
        self.assertIn("search_webs:\n[1]" + self.web["web_content"], history)
        self.assertIn("</turn1>", history)
        self.assertNotIn(self.web["url"], history)
        self.assertNotIn(self.web["content"], history)

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
        self.assertNotIn("search_state_updates", initial_system)
        self.assertIn("信息未闭环时的输出格式", update_system)
        self.assertIn("信息已经闭环时的输出格式", update_system)
        self.assertNotIn("INIT_GOAL", update_system)

        initial_example = initial_system.split(
            "请严格按照以下JSON格式输出：\n", 1
        )[1]
        open_example = update_system.split(
            "## 信息未闭环时的输出格式\n", 1
        )[1].split("\n\n## 信息已经闭环时的输出格式\n", 1)[0]
        closed_example = update_system.split(
            "## 信息已经闭环时的输出格式\n", 1
        )[1]
        self.assertEqual(
            json.loads(initial_example)["search_actions"][0]["search_action"],
            "INIT_GOAL",
        )
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
