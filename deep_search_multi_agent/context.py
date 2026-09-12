"""Prompt context assembly for the two-agent deep-search scheme."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:  # Support both ``python -m deep_search_multi_agent`` and direct script execution.
    from .models import SearchState
    from .prompts import (
        EVIDENCE_EXTRACTOR_SYSTEM_PROMPT,
        PLANNER_INITIAL_SYSTEM_PROMPT,
        PLANNER_TURN_SYSTEM_PROMPT,
    )
except ImportError:  # pragma: no cover - used only by direct script execution.
    from models import SearchState
    from prompts import (
        EVIDENCE_EXTRACTOR_SYSTEM_PROMPT,
        PLANNER_INITIAL_SYSTEM_PROMPT,
        PLANNER_TURN_SYSTEM_PROMPT,
    )


def _clip(text: Any, limit: int) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + f"……（为 prompt 截断，完整原文已保存在轨迹中，共 {len(value)} 字）"


class ContextAssembler:
    def __init__(self, max_web_content_chars: int = 6000):
        if max_web_content_chars <= 0:
            raise ValueError("max_web_content_chars must be positive")
        self.max_web_content_chars = max_web_content_chars

    # ------------------------------------------------------------------
    # Round 1 (Planner only): create the first goal, no observation yet.
    # ------------------------------------------------------------------

    def initial_messages(
        self, user_query: str, current_date: str
    ) -> list[dict[str, str]]:
        content = f"""<constant_info>
user_query: {user_query}
current_date: {current_date}
</constant_info>

<task>
这是第 1 轮。请从原始问题最明显、最基础的信息缺口创建第一个 CREATE_GOAL，并规划搜索 query。
</task>"""
        return [
            {"role": "system", "content": PLANNER_INITIAL_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    # ------------------------------------------------------------------
    # Evidence Extractor: stateless, no history, no state, no conflicts.
    # ------------------------------------------------------------------

    def evidence_extractor_messages(
        self, state: SearchState, pending: dict[str, Any]
    ) -> list[dict[str, str]]:
        goal = state.goals[str(pending["goal_id"])]
        web_blocks: list[str] = []
        for web in pending.get("webs", []) or []:
            if not isinstance(web, dict):
                continue
            web_content = _clip(web.get("web_content"), self.max_web_content_chars)
            web_blocks.append(f"[{web.get('web_id')}]{web_content}")

        content = f"""<constant_info>
user_query: {state.user_query}
current_date: {state.current_date}
</constant_info>

<search_target>
search_goal: {goal.search_goal}
search_focus: {pending.get("search_focus") or ""}
</search_target>

<webs>
{chr(10).join(web_blocks) or "（本轮没有检索到网页）"}
</webs>

<task>
结合 user_query、search_goal、search_focus，从 webs 的网页中抽取对应的证据，并按照规定的 json 格式输出。
</task>"""
        return [
            {"role": "system", "content": EVIDENCE_EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

    # ------------------------------------------------------------------
    # search_history: replay of every completed evidence+planner round.
    # ------------------------------------------------------------------

    def _search_history_text(self, state: SearchState) -> str:
        turn_records = [
            record for record in state.rounds if record.get("kind") == "turn"
        ]
        blocks: list[str] = []
        for index, round_record in enumerate(turn_records, start=1):
            queries = round_record.get("search_queries", []) or []
            query_text = (
                "\n".join(f"- {query}" for query in queries)
                or "（该轮没有搜索 query）"
            )
            evidences = round_record.get("evidences", []) or []
            evidence_text = (
                "\n".join(
                    f"- {evidence.get('evidence_id')}: {evidence.get('statement', '')}"
                    for evidence in evidences
                    if isinstance(evidence, Mapping)
                )
                or "（本轮未产生新证据）"
            )
            conflicts = round_record.get("conflicts", []) or []
            conflict_text = (
                "\n".join(
                    f"- {conflict.get('conflict_id')}: "
                    f"{conflict.get('conflict_object', '')} —— "
                    f"{conflict.get('conflict_statement', '')}"
                    for conflict in conflicts
                    if isinstance(conflict, Mapping)
                )
                or "（本轮未发现新冲突）"
            )
            blocks.append(
                f"<turn{index}>\n"
                + "\n".join(
                    [
                        f"search_goal_id: {round_record.get('target_goal_id', '')}",
                        f"search_goal: {round_record.get('target_goal_text', '')}",
                        "search_queries:",
                        query_text,
                        "evidences:",
                        evidence_text,
                        "conflicts:",
                        conflict_text,
                    ]
                )
                + f"\n</turn{index}>"
            )
        return "\n\n".join(blocks) or "（暂无历史搜索轨迹）"

    # ------------------------------------------------------------------
    # search_status: only frozen (terminal-status) goals, current one excluded.
    # ------------------------------------------------------------------

    def _search_status_text(self, state: SearchState, exclude_goal_id: str) -> str:
        terminal = {"CLOSED", "REFUTED", "UNRESOLVED", "SUPERSEDED"}
        blocks: list[str] = []
        for goal in state.goals.values():
            if goal.search_goal_id == exclude_goal_id:
                continue
            if goal.status not in terminal:
                continue
            blocks.append(
                "\n".join(
                    [
                        f"search_goal_id: {goal.search_goal_id}",
                        f"search_goal: {goal.search_goal}",
                        f"status: {goal.status}",
                        f"supported_statement: {goal.supported_statement}",
                    ]
                )
            )
        return "\n\n".join(blocks) or "（暂无已冻结的历史目标）"

    # ------------------------------------------------------------------
    # current_turn_info: the goal this round updates, its webs, and the
    # evidences the Extractor just produced for those webs (with global ids).
    # ------------------------------------------------------------------

    def _current_turn_info_text(
        self,
        state: SearchState,
        pending: dict[str, Any],
        evidences: list[dict[str, Any]],
    ) -> str:
        goal_id = str(pending["goal_id"])
        goal = state.goals[goal_id]
        queries = pending.get("search_queries", []) or []
        query_text = (
            "\n".join(f"- {query}" for query in queries)
            or "（本轮没有搜索 query）"
        )

        goal_block_lines = [
            "<turn_goal>",
            f"search_goal_id: {goal_id}",
            f"search_goal: {goal.search_goal}",
            "search_queries:",
            query_text,
        ]
        search_focus = pending.get("search_focus")
        if search_focus:
            goal_block_lines.append(f"search_focus: {search_focus}")
        goal_block_lines.append("</turn_goal>")

        web_blocks: list[str] = []
        for web in pending.get("webs", []) or []:
            if not isinstance(web, dict):
                continue
            web_content = _clip(web.get("web_content"), self.max_web_content_chars)
            web_blocks.append(f"[{web.get('web_id')}]{web_content}")

        evidence_lines = []
        for evidence in evidences:
            web_ids: list[str] = []
            seen: set[str] = set()
            for quote_group in evidence.get("quotes", []) or []:
                if not isinstance(quote_group, Mapping):
                    continue
                web_id = str(quote_group.get("web_id"))
                if web_id and web_id not in seen:
                    seen.add(web_id)
                    web_ids.append(web_id)
            refs = "".join(f"[{web_id}]" for web_id in web_ids)
            evidence_lines.append(
                f"{evidence.get('evidence_id')}: {evidence.get('statement', '')} - {refs}"
            )

        turn_webs_lines = [
            "\n\n".join(web_blocks) or "（本轮没有检索到网页）",
        ]
        if evidence_lines:
            turn_webs_lines.extend(["", "evidences:", "\n".join(evidence_lines)])

        return "\n".join(
            goal_block_lines
            + [
                "",
                "<turn_webs>",
                "\n".join(turn_webs_lines),
                "</turn_webs>",
            ]
        )

    # ------------------------------------------------------------------
    # message builder for the Planner's round >= 2 call
    # ------------------------------------------------------------------

    def planner_turn_messages(
        self,
        state: SearchState,
        pending: dict[str, Any],
        evidences: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        goal_id = str(pending["goal_id"])
        next_conflict_id = f"C{len(state.conflicts) + 1}"

        content = f"""<constant_info>
user_query: {state.user_query}
current_date: {state.current_date}
</constant_info>

<search_history>
{self._search_history_text(state)}
</search_history>

<search_status>
{self._search_status_text(state, exclude_goal_id=goal_id)}
</search_status>

<current_turn_info>
{self._current_turn_info_text(state, pending, evidences)}

<id_allocation>
next_conflict_id: {next_conflict_id}
</id_allocation>
</current_turn_info>

<task>
请基于 current_turn_info 中的 turn_webs 和已附带全局编号的证据，识别冲突、更新 turn_goal 这一个目标的 search_state_update，再规划下一步 action（SEARCH / CREATE_GOAL / STOP）。
conflicted_evidence_ids、supported_evidence_ids、blocked_conflict_ids 只能引用 current_turn_info 中存在的编号；新建冲突的 conflict_id 必须从 id_allocation 给出的编号开始递增。
</task>"""
        return [
            {"role": "system", "content": PLANNER_TURN_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
