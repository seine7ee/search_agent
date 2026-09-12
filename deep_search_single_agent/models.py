"""State models for the single-agent deep-search trajectory synthesis scheme.

Unlike ``deep_search_claude`` (a two-agent scheme where a search-planner and
an evidence-processor each get their own model call per turn), this scheme
uses one teacher-model call per round: that single call extracts evidence,
identifies conflicts, updates the state of the goal it was just given
observations for, and decides the next action, all at once.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

try:
    from .metrics import RunMetrics
except ImportError:  # pragma: no cover - direct script execution.
    from metrics import RunMetrics


GOAL_STATUSES = {"OPEN", "CLOSED", "REFUTED", "UNRESOLVED", "SUPERSEDED"}

SEARCH_ACTIONS = {"CREATE_GOAL", "SEARCH", "STOP"}

# A goal derived through at most two SUPERSEDED hops (GOAL_1 -> GOAL_2 ->
# GOAL_3) reaches this depth and may not be superseded again.
MAX_DERIVATION_DEPTH = 2

# Search-round budgets. A goal at the terminal derivation depth gets a single
# extra round instead of the normal three, per the synthesis spec.
DEFAULT_MAX_SEARCHES = 3
TERMINAL_DEPTH_MAX_SEARCHES = 1


@dataclass
class GoalState:
    search_goal_id: str
    search_goal: str
    status: str = "OPEN"
    supported_evidence_ids: list[str] = field(default_factory=list)
    supported_statement: str = ""
    blocked_conflict_ids: list[str] = field(default_factory=list)
    info_gap: str = ""
    depends_on_goal_ids: list[str] = field(default_factory=list)
    supersedes_goal_id: str | None = None
    superseded_by: str | None = None
    derivation_depth: int = 0
    search_round_count: int = 0
    search_queries: list[str] = field(default_factory=list)
    web_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    round_ids: list[int] = field(default_factory=list)

    @property
    def max_searches(self) -> int:
        if self.derivation_depth >= MAX_DERIVATION_DEPTH:
            return TERMINAL_DEPTH_MAX_SEARCHES
        return DEFAULT_MAX_SEARCHES

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["max_searches"] = self.max_searches
        return data


@dataclass
class SearchState:
    user_query: str
    current_date: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    metrics: RunMetrics = field(
        default_factory=lambda: RunMetrics(scheme="scheme_single_agent")
    )
    goals: dict[str, GoalState] = field(default_factory=dict)
    evidences: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    webs: list[dict[str, Any]] = field(default_factory=list)
    # One entry per completed evidence round (round >= 2); round 1 only
    # creates the first goal and has no evidence to record.
    rounds: list[dict[str, Any]] = field(default_factory=list)
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
            "rounds": self.rounds,
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
