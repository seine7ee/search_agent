"""Trajectory state for the stateless single-agent baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from deep_search.metrics import RunMetrics


@dataclass
class BaselineState:
    user_query: str
    current_date: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    metrics: RunMetrics = field(
        default_factory=lambda: RunMetrics(scheme="scheme_b_baseline")
    )
    turns: list[dict[str, Any]] = field(default_factory=list)
    webs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    run_status: str = "RUNNING"
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_query": self.user_query,
            "current_date": self.current_date,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "stop_reason": self.stop_reason,
            "metrics": self.metrics.to_dict(),
            "turns": self.turns,
            "webs": self.webs,
            "errors": self.errors,
        }

    def answer_handoff(self) -> dict[str, Any]:
        return {
            "user_query": self.user_query,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "stop_reason": self.stop_reason,
            "metrics": self.metrics.to_dict(),
            "webs": self.webs,
        }
