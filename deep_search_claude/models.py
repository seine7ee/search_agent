"""State models used by the deep-search orchestrator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

try:
    from .metrics import RunMetrics
except ImportError:  # pragma: no cover - direct script execution.
    from metrics import RunMetrics


GOAL_STATUSES = {
    "OPEN",
    "CLOSED",
    "CLOSED_BY_REFUTED",
    "UNRESOLVED",
    "SUPERSEDED",
    "DROPPED",
}

SEARCH_ACTIONS = {
    "INIT_GOAL",
    "CONTINUE_SEARCH",
    "VERIFY_CONFLICT",
    "EXPAND_GOAL",
    "REFINE_GOAL",
    "STOP",
}


@dataclass
class GoalState:
    search_goal_id: str
    search_goal: str
    status: str = "OPEN"
    status_reason: str = ""
    answer: str = ""
    supported_statement: str = ""
    # Evidence-reference fields use the E* namespace.
    supporting_evidence_ids: list[str] = field(default_factory=list)
    conflict_summary: str = ""
    conflicting_evidence_ids: list[str] = field(default_factory=list)
    evidence_gap: str = ""
    depends_on_goal_ids: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    web_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    # Conflict objects use the separate C* namespace.
    conflict_ids: list[str] = field(default_factory=list)
    turn_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchState:
    user_query: str
    current_date: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    metrics: RunMetrics = field(default_factory=lambda: RunMetrics(scheme="scheme_a"))
    goals: dict[str, GoalState] = field(default_factory=dict)
    evidences: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    webs: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    run_status: str = "RUNNING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_query": self.user_query,
            "current_date": self.current_date,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "stop_reason": self.stop_reason,
            "metrics": self.metrics.to_dict(),
            "goals": [goal.to_dict() for goal in self.goals.values()],
            "evidences": self.evidences,
            "conflicts": self.conflicts,
            "webs": self.webs,
            "turns": self.turns,
            "errors": self.errors,
        }

    def answer_handoff(self) -> dict[str, Any]:
        """Return the compact state consumed by a downstream answer agent."""
        return {
            "user_query": self.user_query,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "stop_reason": self.stop_reason,
            "metrics": self.metrics.to_dict(),
            "search_goals": [goal.to_dict() for goal in self.goals.values()],
            "evidences": self.evidences,
            "conflicts": self.conflicts,
            "webs": self.webs,
        }
