"""Prompt context assembly for both teacher agents.

This is a reworked copy of ``deep_search/context.py``. Compared to the
original, ``search_history`` and ``current_turn_info`` (both only used by the
search-planner update call) are assembled differently:

* ``search_history`` used to replay raw, clipped web content turn by turn.
  It now replays a compact *decision trace* per goal per turn (why the turn
  searched, what evidence/conflicts it produced, what state the planner
  decided, and what it planned next) with no web content and no evidence
  quotes, since that historical replay is only meant to reconstruct past
  reasoning, not to re-supply raw source material.
* ``current_turn_info`` used to be grouped goal-major, printing the same web
  twice whenever two goals happened to cite it, and duplicating each quoted
  excerpt inside an already-included full web body. It is now grouped
  web-major (each web appears once, full content included) with evidence
  entries attached underneath the web(s) that support them, quotes dropped
  (the full web body already carries them), and a per-goal index up front so
  the planner does not have to scan every web block to know which evidence
  ids belong to which goal.

``evidence_messages`` and ``planner_initial_messages`` are unchanged from the
original: this rework only targets the search-planner's own context
assembly, per the goals discussed alongside it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from string import Template
from typing import Any

try:  # Support both ``python -m deep_search_claude`` and direct script execution.
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


# ``$user_query``-style placeholders (not ``{user_query}``) so the long JSON
# examples in the task body can use literal ``{``/``}`` freely without any
# escaping, and so a value being substituted in (e.g. raw web content pulled
# from an external, untrusted search result) can never be mistaken for a
# not-yet-substituted placeholder. ``Template.substitute`` scans the template
# exactly once; unlike chained ``str.replace`` calls, text already inserted
# by an earlier substitution is never re-scanned for later placeholders.
_PLANNER_UPDATE_TEMPLATE = Template(
    """<constant_info>
user_query: $user_query
current_date: $current_date
</constant_info>

<search_history>
$search_history
</search_history>

<search_status>
$search_status
</search_status>

<current_turn_info>
$current_turn_info
</current_turn_info>

<task>
请更新 current_turn_info 中涉及的搜索目标状态，再判断原始问题是否已闭环：若已闭环输出 STOP；否则针对关键缺口规划下一轮动作。
supporting_evidence_ids 和 conflicting_evidence_ids 只能填写 current_turn_info 中存在的 E 开头 evidence_id；VERIFY_CONFLICT.target_conflict_id 只能填写 current_turn_info 中存在的 C 开头 conflict_id。
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
</task>"""
)


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

    # ------------------------------------------------------------------
    # search_history: a compact per-turn, per-goal decision trace.
    # ------------------------------------------------------------------

    @staticmethod
    def _next_action_for_goal(
        next_actions: list[Any], goal_id: str
    ) -> str:
        """Describe what the planner scheduled next for ``goal_id``.

        ``next_actions`` targets goals via different fields depending on the
        action type (``target_goal_id`` for CONTINUE_SEARCH/VERIFY_CONFLICT,
        ``search_goal_id`` for a freshly created REFINE_GOAL replacement).
        A brand-new goal created by INIT_GOAL/EXPAND_GOAL does not describe
        "what happens next" for any *existing* goal, so it is skipped here;
        it gets its own decision trace once it is itself touched by a turn.
        """
        for action in next_actions:
            if not isinstance(action, Mapping):
                continue
            action_type = action.get("search_action")
            reason = str(action.get("action_reason", ""))
            if action_type in {"CONTINUE_SEARCH", "VERIFY_CONFLICT"} and str(
                action.get("target_goal_id")
            ) == goal_id:
                return f"{action_type} — {reason}"
            if action_type == "REFINE_GOAL" and str(
                action.get("target_goal_id")
            ) == goal_id:
                new_goal_id = action.get("search_goal_id", "")
                return f"REFINE_GOAL — 被替换为 {new_goal_id}（{reason}）"
        return "（未在下一轮被安排新的检索动作，可能已闭环、被替换或搁置）"

    def _planner_search_history(self, state: SearchState) -> str:
        """Rebuild each turn's decision trace: motivation -> queries ->
        evidence/conflict found -> resulting state -> next action planned.

        No raw web content and no evidence quotes are included here: this
        section exists to let the planner see *why* it acted and *what it
        concluded*, not to re-supply source material it already digested in
        an earlier turn's ``current_turn_info``.
        """
        turn_blocks: list[str] = []
        for fallback_turn_id, turn in enumerate(state.turns, start=1):
            executions = turn.get("executions", [])
            if not isinstance(executions, list) or not executions:
                continue
            turn_actions = turn.get("search_actions", [])
            if not isinstance(turn_actions, list):
                turn_actions = []
            state_updates = turn.get("state_updates", [])
            if not isinstance(state_updates, list):
                state_updates = []
            next_actions = turn.get("next_search_actions", [])
            if not isinstance(next_actions, list):
                next_actions = []
            state_updates_by_goal = {
                str(update.get("search_goal_id")): update
                for update in state_updates
                if isinstance(update, Mapping)
            }

            goal_blocks: list[str] = []
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

                evidences = execution.get("evidences", [])
                if not isinstance(evidences, list):
                    evidences = []
                evidence_lines = [
                    f"- {evidence.get('evidence_id')}: {evidence.get('statement', '')}"
                    for evidence in evidences
                    if isinstance(evidence, Mapping)
                ]
                evidence_text = (
                    "\n".join(evidence_lines)
                    if evidence_lines
                    else "（本轮未产生新证据）"
                )

                conflicts = execution.get("conflicts", [])
                if not isinstance(conflicts, list):
                    conflicts = []
                conflict_lines = [
                    f"- {conflict.get('conflict_id')}: "
                    f"{conflict.get('conflict_object', '')}"
                    f"（涉及 {', '.join(conflict.get('conflict_evidence_ids', []) or [])}）"
                    for conflict in conflicts
                    if isinstance(conflict, Mapping)
                ]
                updated_conflicts = execution.get("updated_conflicts", [])
                if not isinstance(updated_conflicts, list):
                    updated_conflicts = []
                conflict_lines.extend(
                    f"- {conflict.get('conflict_id')}（补充证据，非新冲突）: "
                    f"当前涉及 {', '.join(conflict.get('conflict_evidence_ids', []) or [])}"
                    for conflict in updated_conflicts
                    if isinstance(conflict, Mapping)
                )
                conflict_text = (
                    "\n".join(conflict_lines)
                    if conflict_lines
                    else "（本轮未发现新冲突）"
                )

                update = state_updates_by_goal.get(goal_id)
                if update is not None:
                    state_after_text = "\n".join(
                        [
                            f"  status: {update.get('status', '')}",
                            f"  status_reason: {update.get('status_reason', '')}",
                            f"  evidence_gap: {update.get('evidence_gap', '')}",
                            f"  conflict_summary: {update.get('conflict_summary', '')}",
                        ]
                    )
                else:
                    state_after_text = "  （该轮未产出状态判定）"

                next_action_text = self._next_action_for_goal(
                    next_actions, goal_id
                )

                goal_blocks.append(
                    "\n".join(
                        [
                            f"search_goal_id: {goal_id}",
                            f"search_goal: {goal_text}",
                            f"search_motivation: {motivation}",
                            "search_queries:",
                            query_text,
                            "evidence_found:",
                            evidence_text,
                            "conflict_found:",
                            conflict_text,
                            "state_after_turn:",
                            state_after_text,
                            f"next_action_planned: {next_action_text}",
                        ]
                    )
                )

            if not goal_blocks:
                continue
            turn_id = turn.get("turn_id", fallback_turn_id)
            turn_blocks.append(
                f"<turn{turn_id}>\n"
                + "\n\n".join(goal_blocks)
                + f"\n</turn{turn_id}>"
            )

        return "\n\n".join(turn_blocks) or "（暂无历史搜索轨迹）"

    # ------------------------------------------------------------------
    # current_turn_info: web-major, evidence attached (no quotes), deduped.
    # ------------------------------------------------------------------

    def _current_turn_info_text(
        self, state: SearchState, executions: list[dict[str, Any]]
    ) -> str:
        goal_header_blocks: list[str] = []
        webs_by_id: dict[str, dict[str, Any]] = {}
        web_order: list[str] = []
        web_to_evidence: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        all_conflicts: list[tuple[str, Mapping[str, Any]]] = []

        for execution in executions:
            goal_id = str(execution.get("search_goal_id", ""))
            goal = state.goals.get(goal_id)
            goal_text = goal.search_goal if goal is not None else ""

            queries = execution.get("search_queries", []) or []
            query_text = (
                "\n".join(f"- {query}" for query in queries)
                or "（本轮没有搜索 query）"
            )

            errors = execution.get("search_errors", []) or []
            error_text = (
                "; ".join(
                    f"{error.get('query', '')} -> {error.get('error', '')}"
                    for error in errors
                    if isinstance(error, Mapping)
                )
                or "（无）"
            )

            evidences = execution.get("evidences", []) or []
            new_evidence_ids = [
                str(evidence.get("evidence_id"))
                for evidence in evidences
                if isinstance(evidence, Mapping)
            ]

            goal_header_blocks.append(
                "\n".join(
                    [
                        f"search_goal_id: {goal_id}",
                        f"search_goal: {goal_text}",
                        "search_queries:",
                        query_text,
                        f"search_errors: {error_text}",
                        f"new_evidence_ids: {', '.join(new_evidence_ids) or '（无）'}",
                    ]
                )
            )

            for web in execution.get("webs", []) or []:
                if not isinstance(web, dict):
                    continue
                web_id = str(web.get("web_id"))
                if web_id not in webs_by_id:
                    webs_by_id[web_id] = web
                    web_order.append(web_id)

            for evidence in evidences:
                if not isinstance(evidence, Mapping):
                    continue
                evidence_goal_id = str(evidence.get("search_goal_id", goal_id))
                cited_web_ids: list[str] = []
                seen_web_ids: set[str] = set()
                for quote_group in evidence.get("quotes", []) or []:
                    if not isinstance(quote_group, Mapping):
                        continue
                    web_id = str(quote_group.get("web_id"))
                    if web_id and web_id not in seen_web_ids:
                        seen_web_ids.add(web_id)
                        cited_web_ids.append(web_id)
                for web_id in cited_web_ids:
                    web_to_evidence.setdefault(web_id, []).append(
                        (evidence_goal_id, evidence)
                    )

            for conflict in execution.get("conflicts", []) or []:
                if isinstance(conflict, Mapping):
                    all_conflicts.append(("new", conflict))
            for conflict in execution.get("updated_conflicts", []) or []:
                if isinstance(conflict, Mapping):
                    all_conflicts.append(("updated", conflict))

        printed_evidence_first_web: dict[str, str] = {}
        web_blocks: list[str] = []
        for web_id in web_order:
            lines = [self._web_for_prompt(webs_by_id[web_id])]
            for evidence_goal_id, evidence in web_to_evidence.get(web_id, []):
                evidence_id = str(evidence.get("evidence_id"))
                if evidence_id in printed_evidence_first_web:
                    first_web_id = printed_evidence_first_web[evidence_id]
                    lines.append(
                        f"  - evidence: 同 {evidence_id}（见 [{first_web_id}]）"
                    )
                else:
                    printed_evidence_first_web[evidence_id] = web_id
                    statement = evidence.get("statement", "")
                    lines.append(
                        f"  - evidence[{evidence_goal_id}/{evidence_id}]: {statement}"
                    )
            web_blocks.append("\n".join(lines))

        conflict_blocks = [
            "\n".join(
                [
                    f"conflict_id: {conflict.get('conflict_id')}",
                    f"kind: {'新冲突' if kind == 'new' else '已有冲突补充证据（非新冲突）'}",
                    f"search_goal_id: {conflict.get('search_goal_id')}",
                    f"conflict_status: {conflict.get('conflict_status', '')}",
                    f"conflict_object: {conflict.get('conflict_object', '')}",
                    "conflict_evidence_ids: "
                    + ", ".join(conflict.get("conflict_evidence_ids", []) or []),
                ]
            )
            for kind, conflict in all_conflicts
        ]

        return "\n".join(
            [
                "<turn_goals>",
                "\n\n".join(goal_header_blocks) or "（本轮没有涉及的搜索目标）",
                "</turn_goals>",
                "",
                "<turn_webs>",
                "\n\n".join(web_blocks) or "（本轮没有检索到网页）",
                "</turn_webs>",
                "",
                "<turn_conflicts>",
                "\n\n".join(conflict_blocks) or "（本轮无冲突）",
                "</turn_conflicts>",
            ]
        )

    # ------------------------------------------------------------------
    # message builders
    # ------------------------------------------------------------------

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
        prior_conflicts = [
            {
                "conflict_id": conflict.get("conflict_id"),
                "conflict_object": conflict.get("conflict_object"),
                "conflict_status": conflict.get("conflict_status"),
                "conflict_evidence_ids": conflict.get("conflict_evidence_ids", []),
            }
            for conflict in state.conflicts
            if conflict.get("search_goal_id") == goal_id
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

<prior_conflicts>
{_json(prior_conflicts)}
</prior_conflicts>

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

        search_history = self._planner_search_history(state)
        current_turn_info = self._current_turn_info_text(state, executions)

        content = _PLANNER_UPDATE_TEMPLATE.substitute(
            user_query=state.user_query,
            current_date=state.current_date,
            search_history=search_history,
            search_status=_json(goal_summary),
            current_turn_info=current_turn_info,
        )
        return [
            {"role": "system", "content": SEARCH_PLANNER_UPDATE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
