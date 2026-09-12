"""Run metrics for the two-agent (evidence extractor + planner) synthesis scheme."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunMetrics:
    scheme: str
    total_rounds: int = 0
    search_rounds: int = 0
    model_call_count: int = 0
    planner_call_count: int = 0
    evidence_extractor_call_count: int = 0
    search_query_count: int = 0
    web_result_count: int = 0
    unique_web_count: int = 0
    goal_created_count: int = 0
    conflict_search_count: int = 0
    termination_type: str = "RUNNING"
    final_action: str | None = None
    last_model_action: str | None = None
    started_at: str = field(default_factory=_utc_now)
    finished_at: str | None = None
    elapsed_ms: float | None = None
    _started_monotonic: float = field(default_factory=time.perf_counter, repr=False)

    def finish(self, termination_type: str, final_action: str) -> None:
        self.termination_type = termination_type
        self.final_action = final_action
        self.finished_at = _utc_now()
        self.elapsed_ms = round(
            (time.perf_counter() - self._started_monotonic) * 1000, 2
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "total_rounds": self.total_rounds,
            "search_rounds": self.search_rounds,
            "model_call_count": self.model_call_count,
            "planner_call_count": self.planner_call_count,
            "evidence_extractor_call_count": self.evidence_extractor_call_count,
            "search_query_count": self.search_query_count,
            "web_result_count": self.web_result_count,
            "unique_web_count": self.unique_web_count,
            "goal_created_count": self.goal_created_count,
            "conflict_search_count": self.conflict_search_count,
            "termination_type": self.termination_type,
            "terminated_by_model": self.termination_type == "MODEL_STOP",
            "reached_max_rounds": self.termination_type == "MAX_ROUNDS",
            "final_action": self.final_action,
            "last_model_action": self.last_model_action,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms,
        }
