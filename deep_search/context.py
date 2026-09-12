"""Prompt context assembly for both teacher agents."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

try:  # Support both ``python -m deep_search`` and direct script execution.
    from .models import SearchState
    from .prompts import (
        EVIDENCE_PROCESSOR_SYSTEM_PROMPT,
        SEARCH_PLANNER_INITIAL_SYSTEM_PROMPT,
        SEARCH_PLANNER_UPDATE_SYSTEM_PROMPT,
    )
except ImportError:  # pragma: no cover - used only by direct script execution.
    from models import SearchState
    from prompts import (
        EVIDENCE_PROCESSOR_SYSTEM_PROMPT,
        SEARCH_PLANNER_INITIAL_SYSTEM_PROMPT,
        SEARCH_PLANNER_UPDATE_SYSTEM_PROMPT,
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _clip(text: Any, limit: int) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + f"……（为 prompt 截断，完整原文已保存在轨迹中，共 {len(value)} 字）"


def format_webs(webs: Iterable[dict[str, Any]], content_limit: int) -> str:
    blocks: list[str] = []
    for web in webs:
        web_content = _clip(web.get("web_content"), content_limit)
        blocks.append(f"[{web['web_id']}]{web_content}")
    return "\n\n".join(blocks) if blocks else "（本轮没有检索到网页）"


class ContextAssembler:
    def __init__(self, max_web_content_chars: int = 6000):
        if max_web_content_chars <= 0:
            raise ValueError("max_web_content_chars must be positive")
        self.max_web_content_chars = max_web_content_chars

    def _web_for_prompt(self, web: dict[str, Any]) -> str:
        """Expose only the ID and assembled web_content to the planner."""
        web_content = _clip(
            web.get("web_content"), self.max_web_content_chars
        )
        return f"[{web.get('web_id')}]{web_content}"

    def _planner_search_history(self, state: SearchState) -> str:
        """Rebuild completed search turns in a compact, readable form."""
        turn_blocks: list[str] = []
        for fallback_turn_id, turn in enumerate(state.turns, start=1):
            executions = turn.get("executions", [])
            if not isinstance(executions, list) or not executions:
                continue
            turn_actions = turn.get("search_actions", [])
            if not isinstance(turn_actions, list):
                turn_actions = []

            execution_blocks: list[str] = []
            for action_index, execution in enumerate(executions):
                if not isinstance(execution, Mapping):
                    continue
                execution_action = execution.get("search_action")
                if not isinstance(execution_action, Mapping):
                    execution_action = (
                        turn_actions[action_index]
                        if action_index < len(turn_actions)
                        and isinstance(turn_actions[action_index], Mapping)
                        else {}
                    )

                goal_id = str(execution.get("search_goal_id", ""))
                goal = state.goals.get(goal_id)
                goal_text = (
                    goal.search_goal
                    if goal is not None
                    else str(execution_action.get("search_goal", ""))
                )
                motivation = str(execution_action.get("action_reason", ""))

                queries = execution.get("search_queries", [])
                if not isinstance(queries, list):
                    queries = []
                query_text = "\n".join(f"- {query}" for query in queries)
                if not query_text:
                    query_text = "（该轮没有搜索 query）"

                webs = execution.get("webs", [])
                if not isinstance(webs, list):
                    webs = []
                web_text = "\n".join(
                    self._web_for_prompt(web)
                    for web in webs
                    if isinstance(web, dict)
                )
                if not web_text:
                    web_text = "（该轮没有检索到网页）"

                execution_blocks.append(
                    "\n".join(
                        [
                            f"search_goal_id: {goal_id}",
                            f"search_goal: {goal_text}",
                            f"search_motivation: {motivation}",
                            "search_queries:",
                            query_text,
                            "search_webs:",
                            web_text,
                        ]
                    )
                )

            if not execution_blocks:
                continue
            turn_id = turn.get("turn_id", fallback_turn_id)
            turn_blocks.append(
                f"<turn{turn_id}>\n"
                + "\n\n".join(execution_blocks)
                + f"\n</turn{turn_id}>"
            )

        return "\n\n".join(turn_blocks) or "（暂无历史搜索轨迹）"

    def planner_initial_messages(
        self, user_query: str, current_date: str
    ) -> list[dict[str, str]]:
        content = f"""<constant_info>
user_query: {user_query}
current_date: {current_date}
</constant_info>

<task>
这是首轮。请从原始问题最明显、最基础的信息缺口创建第一个 INIT_GOAL，并规划搜索 query。
</task>"""
        return [
            {"role": "system", "content": SEARCH_PLANNER_INITIAL_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    def evidence_messages(
        self,
        state: SearchState,
        goal_id: str,
        current_webs: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        goal = state.goals[goal_id]
        prior_evidences = [
            evidence
            for evidence in state.evidences
            if evidence.get("search_goal_id") == goal_id
        ]
        prior_state = {
            "status": goal.status,
            "answer": goal.answer,
            "supported_statement": goal.supported_statement,
            "conflict_summary": goal.conflict_summary,
            "evidence_gap": goal.evidence_gap,
        }
        content = f"""<constant_info>
user_query: {state.user_query}
current_date: {state.current_date}
</constant_info>

<current_search_goal>
search_goal_id: {goal.search_goal_id}
search_goal: {goal.search_goal}
</current_search_goal>

<prior_goal_state>
{_json(prior_state)}
</prior_goal_state>

<prior_evidences>
{_json(prior_evidences)}
</prior_evidences>

<id_allocation>
next_evidence_id: E{len(state.evidences) + 1}
next_conflict_id: C{len(state.conflicts) + 1}
</id_allocation>

<current_search_webs>
{format_webs(current_webs, self.max_web_content_chars)}
</current_search_webs>"""
        return [
            {"role": "system", "content": EVIDENCE_PROCESSOR_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    def planner_update_messages(
        self, state: SearchState, executions: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        planner_updated_goal_ids = {
            update.get("search_goal_id")
            for turn in state.turns
            for update in turn.get("state_updates", [])
            if isinstance(update, dict)
        }
        goal_summary: list[dict[str, Any]] = []
        for goal in state.goals.values():
            if goal.search_goal_id not in planner_updated_goal_ids:
                goal_summary.append(
                    {
                        "search_goal_id": goal.search_goal_id,
                        "search_goal": goal.search_goal,
                        "in_progress": True,
                    }
                )
                continue
            goal_summary.append(
                {
                    "search_goal_id": goal.search_goal_id,
                    "search_goal": goal.search_goal,
                    "status": goal.status,
                    "answer": goal.answer,
                    "conflict_summary": goal.conflict_summary,
                    "evidence_gap": goal.evidence_gap,
                }
            )
        current_turn: list[dict[str, Any]] = []
        for execution in executions:
            current_turn.append(
                {
                    "search_goal_id": execution["search_goal_id"],
                    "search_goal": state.goals[
                        execution["search_goal_id"]
                    ].search_goal,
                    "search_queries": execution["search_queries"],
                    "search_errors": execution["search_errors"],
                    "search_webs": [
                        self._web_for_prompt(web) for web in execution["webs"]
                    ],
                    "evidences": execution["evidences"],
                    "conflicts": execution["conflicts"],
                }
            )

        search_history = self._planner_search_history(state)

        content = ("""<constant_info>
user_query: {state.user_query}
current_date: {state.current_date}
</constant_info>

<search_history>
{search_history}
</search_history>

<search_status>
{search_status}
</search_status>

<current_turn_info>
{current_turn_info}
</current_turn_info>

<task>
请更新 current_turn_info 中涉及的搜索目标状态，再判断原始问题是否已闭环：若已闭环输出 STOP；否则针对关键缺口规划下一轮动作。
supporting_evidence_ids 和 conflicting_evidence_ids 只能填写上下文中存在的 E 开头 evidence_id；VERIFY_CONFLICT.target_conflict_id 只能填写 C 开头 conflict_id。
只输出 current_turn_info 中本轮涉及目标的 search_state_updates，不要重复更新其他历史目标。
search_state_updates 不得输出 depends_on_goal_ids；目标依赖关系由编排器维护，状态判定时不可修改。

# 请严格按照以下格式输出
## JSON 输出硬约束
1. 只输出一个可被标准 JSON 解析器直接解析的 JSON 对象；不要 Markdown 代码块、注释、解释性前后缀或尾随逗号。
2. 所有 key 和字符串必须使用双引号；空值使用 null，不得使用 None、单引号或 NaN。
3. 顶层必须同时包含 search_state_updates 和 next_turn_search_actions；两者不得改名、缺失或额外嵌套。
4. search_state_updates 必须是对象数组；next_turn_search_actions 必须是对象，其中 search_actions 必须是非空对象数组。

### 信息未闭环时的输出格式
{
  "search_state_updates": [{
    "search_goal_id": "GOAL_1",
    "search_goal": "原搜索目标",
    "status": "OPEN|CLOSED|CLOSED_BY_REFUTED|UNRESOLVED|SUPERSEDED|DROPPED",
    "status_reason": "状态原因",
    "answer": "当前直接答案；OPEN 时可为部分答案",
    "supported_statement": "证据支持的归一化事实",
    "supporting_evidence_ids": ["E1"],
    "conflict_summary": "未解决且影响答案的冲突",
    "conflicting_evidence_ids": ["E2"],
    "evidence_gap": "距离闭环还缺少什么"
  }],
  "next_turn_search_actions": {
    "action_reason": "下一轮整体规划理由",
    "search_actions": [{
      "search_action": "CONTINUE_SEARCH",
      "action_reason": "为什么需要继续搜索",
      "target_goal_id": "GOAL_1",
      "search_queries": ["query 1", "query 2"]
    }]
  }
}

### 信息已经闭环时的输出格式
{
  "search_state_updates": [{
    "search_goal_id": "GOAL_1",
    "search_goal": "原搜索目标",
    "status": "CLOSED",
    "status_reason": "已有充分证据且不存在阻塞冲突",
    "answer": "当前直接答案",
    "supported_statement": "证据支持的归一化事实",
    "supporting_evidence_ids": ["E1"],
    "conflict_summary": "",
    "conflicting_evidence_ids": [],
    "evidence_gap": ""
  }],
  "next_turn_search_actions": {
    "action_reason": "所有回答原始问题所必需的目标均已闭环",
    "search_actions": [{
      "search_action": "STOP",
      "action_reason": "所有必要目标均已闭环"
    }]
  }
}
</task>""".replace("{state.user_query}", state.user_query)
                   .replace("{state.current_date}", state.current_date)
                   .replace("{search_history}", search_history)
                   .replace("{search_status}", _json(goal_summary))
                   .replace("{current_turn_info}", _json(current_turn)))
        return [
            {"role": "system", "content": SEARCH_PLANNER_UPDATE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
