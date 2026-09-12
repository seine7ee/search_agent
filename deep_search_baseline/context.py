"""History context assembly for the single-agent baseline."""

from __future__ import annotations

import json
from typing import Any

from .models import BaselineState
from .prompts import BASELINE_SYSTEM_PROMPT


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"……（Prompt 截断，完整内容保存在轨迹中，共 {len(text)} 字）"


class BaselineContextAssembler:
    def __init__(self, max_web_content_chars: int = 6000):
        if max_web_content_chars <= 0:
            raise ValueError("max_web_content_chars must be positive")
        self.max_web_content_chars = max_web_content_chars

    def _history_for_prompt(self, state: BaselineState) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        for turn in state.turns:
            if turn.get("decision") != "SEARCH":
                continue
            actions: list[dict[str, Any]] = []
            for execution in turn.get("search_actions", []):
                actions.append(
                    {
                        "search_goal": execution["search_goal"],
                        "search_queries": execution["search_queries"],
                        "search_webs": [
                            f"[{web['web_id']}]"
                            + _clip(
                                web.get("web_content"),
                                self.max_web_content_chars,
                            )
                            for web in execution["webs"]
                        ],
                        "search_errors": execution["search_errors"],
                    }
                )
            history.append({"turn_id": turn["turn_id"], "search_actions": actions})
        return history

    def messages(self, state: BaselineState) -> list[dict[str, str]]:
        history = self._history_for_prompt(state)
        content = f"""<constant_info>
user_query: {state.user_query}
current_date: {state.current_date}
</constant_info>

<search_history>
{json.dumps(history, ensure_ascii=False, indent=2) if history else "（尚无历史搜索）"}
</search_history>

<task>
请观察全部历史搜索结果，判断当前应继续 SEARCH 还是已经可以 STOP，并严格按对应 JSON 协议输出。
</task>"""
        return [
            {"role": "system", "content": BASELINE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
