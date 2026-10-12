"""Prompt context assembly for the web_info_extractor + planner scheme (v1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:  # Support both ``python -m deep_search_multi_agent_v1`` and direct script execution.
    from .models import SearchState
    from .prompts import PLANNER_INITIAL_SYSTEM_PROMPT, PLANNER_TURN_SYSTEM_PROMPT
except ImportError:  # pragma: no cover - used only by direct script execution.
    from models import SearchState
    from prompts import PLANNER_INITIAL_SYSTEM_PROMPT, PLANNER_TURN_SYSTEM_PROMPT


def format_compressed_web(web: Mapping[str, Any]) -> str:
    """Render one Web Info Extractor-compressed page the way the Planner sees it.

    Reconstructs the same "网页来源: ...｜网页内容：..." shape the raw
    web_content already used, from the individual metadata fields every
    search backend attaches to a result, so this works regardless of which
    backend produced the page (only the Baidu adapter attaches
    authority_level; it is included when present, omitted otherwise).
    """
    parts = [f"网页来源: {web.get('website', '')}", f"网页时间: {web.get('date', '')}"]
    authority_level = web.get("authority_level")
    if authority_level:
        parts.append(f"权威性: {authority_level}")
    parts.append(f"网页标题: {web.get('title', '')}")
    header = "｜".join(parts)
    return f"[{web.get('web_id')}]{header}｜网页内容：{web.get('compressed_content', '')}"


class ContextAssembler:
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
    # search_history: replay of every completed round, webs already compressed.
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
            webs = round_record.get("compressed_webs", []) or []
            web_text = (
                "\n".join(format_compressed_web(web) for web in webs)
                or "（本轮没有可用网页）"
            )
            lines = [
                f"search_goal_id: {round_record.get('target_goal_id', '')}",
                f"search_goal: {round_record.get('target_goal_text', '')}",
                "search_queries:",
                query_text,
                "webs:",
                web_text,
            ]
            conflicts = round_record.get("conflicts", []) or []
            if conflicts:
                lines.append("conflicts:")
                lines.extend(
                    f"- {conflict.get('conflict_id')}: "
                    f"{conflict.get('conflict_object', '')} —— "
                    f"{conflict.get('conflict_statement', '')}"
                    for conflict in conflicts
                    if isinstance(conflict, Mapping)
                )
            blocks.append(f"<turn{index}>\n" + "\n".join(lines) + f"\n</turn{index}>")
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
    # current_turn_info: the goal this round updates and its compressed webs.
    # ------------------------------------------------------------------

    def _current_turn_info_text(
        self, state: SearchState, pending: dict[str, Any]
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

        webs = pending.get("compressed_webs", []) or []
        web_text = (
            "\n".join(format_compressed_web(web) for web in webs)
            or "（本轮没有可用网页：检索结果均与目标无关，或检索失败）"
        )

        return "\n".join(
            goal_block_lines
            + [
                "",
                "<turn_webs>",
                web_text,
                "</turn_webs>",
            ]
        )

    # ------------------------------------------------------------------
    # message builder for the Planner's round >= 2 call
    # ------------------------------------------------------------------

    def planner_turn_messages(
        self, state: SearchState, pending: dict[str, Any]
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
{self._current_turn_info_text(state, pending)}

<id_allocation>
next_conflict_id: {next_conflict_id}
</id_allocation>
</current_turn_info>

<task>
请基于 current_turn_info 中的 turn_webs（已由 Web Info Extractor 按 turn_goal 压缩），识别冲突、更新 turn_goal 这一个目标的 search_state_update，再规划下一步 action（SEARCH / CREATE_GOAL / STOP）。
blocked_conflict_ids 只能引用 current_turn_info 中存在的编号；新建冲突的 conflict_id 由系统从 id_allocation 给出的编号开始递增分配，你不需要、也不能自己输出 conflict_id。
</task>"""
        return [
            {"role": "system", "content": PLANNER_TURN_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
