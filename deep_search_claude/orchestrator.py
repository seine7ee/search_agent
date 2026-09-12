"""Orchestration layer for two-agent deep-search trajectory synthesis."""

from __future__ import annotations

import copy
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

try:  # Support both package and direct script execution.
    from .context import ContextAssembler
    from .models import GOAL_STATUSES, SEARCH_ACTIONS, GoalState, SearchState
    from .storage import save_json_atomic
except ImportError:  # pragma: no cover - used only by direct script execution.
    from context import ContextAssembler
    from models import GOAL_STATUSES, SEARCH_ACTIONS, GoalState, SearchState
    from storage import save_json_atomic

ModelRequester = Callable[[list[dict[str, str]]], Mapping[str, Any]]
WebSearcher = Callable[..., list[dict[str, Any]]]


class ProtocolError(ValueError):
    """Raised when a teacher model violates the structured protocol."""


@dataclass(frozen=True)
class OrchestratorConfig:
    max_turns: int = 8
    top_k: int = 10
    max_queries_per_action: int = 5
    max_web_content_chars: int = 6000
    model_max_attempts: int = 3
    model_retry_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.max_queries_per_action <= 0:
            raise ValueError("max_queries_per_action must be positive")
        if self.model_max_attempts <= 0:
            raise ValueError("model_max_attempts must be positive")
        if self.model_retry_delay_seconds < 0:
            raise ValueError("model_retry_delay_seconds cannot be negative")


class DeepSearchOrchestrator:
    def __init__(
        self,
        request_model: ModelRequester,
        web_search: WebSearcher,
        *,
        config: OrchestratorConfig | None = None,
        output_path: str | Path | None = None,
        logger: logging.Logger | None = None,
    ):
        self.request_model = request_model
        self.web_search = web_search
        self.config = config or OrchestratorConfig()
        self.output_path = Path(output_path) if output_path else None
        self.context = ContextAssembler(self.config.max_web_content_chars)
        self._web_key_to_id: dict[str, str] = {}
        self.logger = logger or logging.getLogger("deep_search.orchestrator")
        self._active_state: SearchState | None = None
        self._active_turn: dict[str, Any] | None = None
        self._last_model_call: dict[str, Any] | None = None
        self._stage = "idle"

    def run(self, user_query: str, current_date: str | None = None) -> SearchState:
        """Run synthesis and persist diagnostic state even when the run fails."""
        self._active_state = None
        self._active_turn = None
        self._last_model_call = None
        self._stage = "initialize"
        try:
            return self._run_impl(user_query, current_date=current_date)
        except Exception as exc:
            failure = {
                "stage": self._stage,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            if self._last_model_call is not None:
                failure["last_model_call"] = copy.deepcopy(self._last_model_call)
            state = self._active_state
            if state is not None:
                state.run_status = "FAILED"
                state.stop_reason = f"{type(exc).__name__}: {exc}"
                self._finish_metrics(state, "FAILED", "FAILED")
                state.errors.append(failure)
                if self._active_turn is not None:
                    active_turn_id = self._active_turn.get("turn_id")
                    if not state.turns or state.turns[-1].get("turn_id") != active_turn_id:
                        failed_turn = copy.deepcopy(self._active_turn)
                        failed_turn["failure"] = failure
                        state.turns.append(failed_turn)
                try:
                    self._checkpoint(state)
                except Exception:
                    self.logger.exception(
                        "failed_to_persist_failure_trajectory",
                        extra={
                            "event": "checkpoint_failed",
                            "run_id": state.run_id,
                            "stage": self._stage,
                        },
                    )
            self.logger.exception(
                "deep_search_run_failed: stage=%s error=%s",
                self._stage,
                exc,
                extra={
                    "event": "run_failed",
                    "run_id": state.run_id if state else None,
                    "stage": self._stage,
                    "error_type": type(exc).__name__,
                    "last_model_call": self._last_model_call,
                },
            )
            raise

    def _run_impl(
        self, user_query: str, current_date: str | None = None
    ) -> SearchState:
        query = user_query.strip()
        if not query:
            raise ValueError("user_query cannot be empty")
        state = SearchState(
            user_query=query,
            current_date=current_date or date.today().isoformat(),
        )
        self._active_state = state
        self._web_key_to_id = {}
        self.logger.info(
            "deep_search_run_started",
            extra={
                "event": "run_started",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "user_query": state.user_query,
                "current_date": state.current_date,
                "max_turns": self.config.max_turns,
                "top_k": self.config.top_k,
                "output_path": str(self.output_path) if self.output_path else None,
            },
        )

        self._stage = "initial_planner"
        initial_messages = self.context.planner_initial_messages(
            state.user_query, state.current_date
        )
        initial_response = self._call_model(initial_messages, "search_planner")
        actions = self._extract_actions(initial_response, initial=True)
        self._validate_actions(actions, state, initial=True)
        pending_planner = {
            "messages": initial_messages,
            "response": initial_response,
            "phase": "initial_plan",
        }

        for turn_index in range(1, self.config.max_turns + 1):
            self._stage = "turn_started"
            state.metrics.total_turns = turn_index
            state.metrics.last_model_action = (
                "STOP" if self._is_stop(actions) else "SEARCH"
            )
            if not self._is_stop(actions):
                state.metrics.search_turns += 1
            turn: dict[str, Any] = {
                "turn_id": turn_index,
                "planner_input": pending_planner,
                "search_actions": copy.deepcopy(actions),
                "executions": [],
            }
            self._active_turn = turn
            self.logger.info(
                "turn_started: turn=%s actions=%s",
                turn_index,
                action_types := [action.get("search_action") for action in actions],
                extra={
                    "event": "turn_started",
                    "run_id": state.run_id,
                    "turn_id": turn_index,
                    "action_types": action_types,
                },
            )

            if self._is_stop(actions):
                state.stop_reason = actions[0].get(
                    "action_reason", "Search planner requested STOP"
                )
                state.run_status = "COMPLETED"
                self._finish_metrics(state, "MODEL_STOP", "STOP")
                turn["state_updates"] = []
                state.turns.append(turn)
                self._checkpoint(state)
                self.logger.info(
                    "deep_search_run_completed",
                    extra={
                        "event": "run_completed",
                        "run_id": state.run_id,
                        "turn_id": turn_index,
                        "stop_reason": state.stop_reason,
                    },
                )
                return state

            self._stage = "apply_search_actions"
            self._apply_new_goals(actions, state, turn_index)
            executions = [
                self._execute_action(action, state, turn_index) for action in actions
            ]
            turn["executions"] = copy.deepcopy(executions)

            self._stage = "planner_state_update"
            update_messages = self.context.planner_update_messages(state, executions)
            update_response = self._call_model(update_messages, "search_planner")
            state_updates, next_actions = self._extract_update(update_response)
            self.logger.info(
                "planner_state_updates_received: updates=%s next_actions=%s",
                len(state_updates),
                [action.get("search_action") for action in next_actions],
                extra={
                    "event": "planner_state_updates_received",
                    "run_id": state.run_id,
                    "turn_id": turn_index,
                    "state_update_summaries": [
                        {
                            "search_goal_id": update.get("search_goal_id"),
                            "status": update.get("status"),
                            "supporting_evidence_ids": update.get(
                                "supporting_evidence_ids"
                            )
                            or update.get("supported_evidence_ids", []),
                            "conflicting_evidence_ids": update.get(
                                "conflicting_evidence_ids", []
                            ),
                        }
                        for update in state_updates
                    ],
                    "next_action_types": [
                        action.get("search_action") for action in next_actions
                    ],
                },
            )
            updated_goal_ids = {
                update.get("search_goal_id") for update in state_updates
            }
            executed_goal_ids = {
                execution["search_goal_id"] for execution in executions
            }
            missing_updates = executed_goal_ids - updated_goal_ids
            if missing_updates:
                raise ProtocolError(
                    "search planner omitted state updates for: "
                    + ", ".join(sorted(missing_updates))
                )
            self._apply_state_updates(state_updates, state)
            self._apply_conflict_status(state, updated_goal_ids, next_actions)
            self._validate_actions(next_actions, state, initial=False)

            turn["state_updates"] = copy.deepcopy(state_updates)
            turn["planner_output"] = {
                "messages": update_messages,
                "response": update_response,
                "phase": "state_update_and_next_plan",
            }
            turn["next_search_actions"] = copy.deepcopy(next_actions)
            state.turns.append(turn)
            self._active_turn = None
            self._checkpoint(state)

            if self._is_stop(next_actions):
                state.stop_reason = next_actions[0].get(
                    "action_reason", "Search planner requested STOP"
                )
                state.run_status = "COMPLETED"
                state.metrics.last_model_action = "STOP"
                self._finish_metrics(state, "MODEL_STOP", "STOP")
                self._checkpoint(state)
                self.logger.info(
                    "deep_search_run_completed",
                    extra={
                        "event": "run_completed",
                        "run_id": state.run_id,
                        "turn_id": turn_index,
                        "stop_reason": state.stop_reason,
                        "goal_count": len(state.goals),
                        "evidence_count": len(state.evidences),
                    },
                )
                return state

            actions = next_actions
            pending_planner = turn["planner_output"]

        state.run_status = "MAX_TURNS_REACHED"
        state.stop_reason = f"Reached max_turns={self.config.max_turns}"
        self._finish_metrics(state, "MAX_TURNS", "MAX_TURNS_REACHED")
        self._checkpoint(state)
        self.logger.warning(
            "deep_search_max_turns_reached",
            extra={
                "event": "max_turns_reached",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "max_turns": self.config.max_turns,
            },
        )
        return state

    def _call_model(
        self, messages: list[dict[str, str]], agent_name: str
    ) -> dict[str, Any]:
        state = self._active_state
        call_record: dict[str, Any] = {
            "agent": agent_name,
            "stage": self._stage,
            # One logical model call may have several physical attempts. Store the
            # request messages once here, never inside individual attempt records.
            "messages": copy.deepcopy(messages),
            "attempts": [],
        }
        self._last_model_call = call_record
        for attempt in range(1, self.config.model_max_attempts + 1):
            if state is not None:
                state.metrics.model_call_count += 1
            started_at = time.perf_counter()
            self.logger.info(
                "model_call_started: agent=%s stage=%s attempt=%s/%s",
                agent_name,
                self._stage,
                attempt,
                self.config.model_max_attempts,
                extra={
                    "event": "model_call_started",
                    "run_id": state.run_id if state else None,
                    "turn_id": self._active_turn.get("turn_id")
                    if self._active_turn
                    else None,
                    "agent": agent_name,
                    "stage": self._stage,
                    "attempt": attempt,
                    "max_attempts": self.config.model_max_attempts,
                    "message_count": len(messages),
                    "prompt_chars": sum(
                        len(item.get("content", "")) for item in messages
                    ),
                },
            )
            try:
                response = self.request_model(messages)
                if not isinstance(response, Mapping):
                    raise ProtocolError(f"{agent_name} must return a JSON object")
            except (ValueError, ProtocolError) as exc:
                call_record["attempts"].append(
                    {
                        "attempt": attempt,
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                call_record["attempt"] = attempt
                call_record["attempt_count"] = attempt
                if attempt >= self.config.model_max_attempts:
                    call_record["status"] = "FAILED"
                    call_record["error_type"] = type(exc).__name__
                    call_record["error"] = str(exc)
                    raise
                call_record["status"] = "RETRYING"
                delay = self.config.model_retry_delay_seconds * (2 ** (attempt - 1))
                self.logger.warning(
                    "model_call_retrying: agent=%s stage=%s attempt=%s error=%s",
                    agent_name,
                    self._stage,
                    attempt,
                    exc,
                    extra={
                        "event": "model_call_retrying",
                        "run_id": state.run_id if state else None,
                        "turn_id": self._active_turn.get("turn_id")
                        if self._active_turn
                        else None,
                        "agent": agent_name,
                        "stage": self._stage,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "retry_delay_seconds": delay,
                        "error_type": type(exc).__name__,
                    },
                )
                if delay:
                    time.sleep(delay)
                continue

            normalized = copy.deepcopy(dict(response))
            call_record["attempts"].append(
                {"attempt": attempt, "status": "SUCCEEDED"}
            )
            call_record["attempt"] = attempt
            call_record["attempt_count"] = attempt
            call_record["status"] = "SUCCEEDED"
            call_record["response"] = copy.deepcopy(normalized)
            call_record.pop("error_type", None)
            call_record.pop("error", None)
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
            self.logger.info(
                "model_call_completed: agent=%s duration_ms=%s keys=%s",
                agent_name,
                elapsed_ms,
                list(normalized),
                extra={
                    "event": "model_call_completed",
                    "run_id": state.run_id if state else None,
                    "turn_id": self._active_turn.get("turn_id")
                    if self._active_turn
                    else None,
                    "agent": agent_name,
                    "stage": self._stage,
                    "attempt": attempt,
                    "duration_ms": elapsed_ms,
                    "response_keys": list(normalized),
                },
            )
            self.logger.info(
                "model_response",
                extra={
                    "event": "model_response",
                    "run_id": state.run_id if state else None,
                    "turn_id": self._active_turn.get("turn_id")
                    if self._active_turn
                    else None,
                    "agent": agent_name,
                    "stage": self._stage,
                    "attempt": attempt,
                    "model_response": normalized,
                },
            )
            return normalized

        raise RuntimeError("model retry loop exited unexpectedly")  # pragma: no cover

    def _extract_actions(
        self, response: Mapping[str, Any], *, initial: bool
    ) -> list[dict[str, Any]]:
        actions = response.get("search_actions")
        if not isinstance(actions, list) or not actions:
            phase = "initial" if initial else "next-turn"
            raise ProtocolError(f"{phase} search_actions must be a non-empty array")
        if not all(isinstance(item, Mapping) for item in actions):
            raise ProtocolError("every search action must be an object")
        return [dict(item) for item in actions]

    def _extract_update(
        self, response: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        updates = response.get("search_state_updates", [])
        if not isinstance(updates, list) or not all(
            isinstance(item, Mapping) for item in updates
        ):
            raise ProtocolError("search_state_updates must be an array of objects")

        next_part = response.get("next_turn_search_actions")
        if isinstance(next_part, Mapping):
            actions = next_part.get("search_actions")
            shared_reason = next_part.get("action_reason")
        elif isinstance(next_part, list):
            actions = next_part
            shared_reason = None
        else:
            actions = response.get("search_actions")
            shared_reason = None
        if not isinstance(actions, list) or not actions:
            raise ProtocolError("next_turn_search_actions.search_actions is required")
        normalized_actions = [dict(item) for item in actions if isinstance(item, Mapping)]
        if len(normalized_actions) != len(actions):
            raise ProtocolError("every next-turn search action must be an object")
        if shared_reason:
            for action in normalized_actions:
                action.setdefault("action_reason", shared_reason)
        return [dict(item) for item in updates], normalized_actions

    def _validate_actions(
        self, actions: list[dict[str, Any]], state: SearchState, *, initial: bool
    ) -> None:
        action_types = [action.get("search_action") for action in actions]
        unknown = [value for value in action_types if value not in SEARCH_ACTIONS]
        if unknown:
            raise ProtocolError(f"unknown search actions: {unknown}")
        if "STOP" in action_types and len(actions) != 1:
            raise ProtocolError("STOP must be the only search action")
        if "STOP" in action_types and any(
            goal.status == "OPEN" for goal in state.goals.values()
        ):
            raise ProtocolError("STOP is not allowed while a search goal is OPEN")
        if initial and action_types != ["INIT_GOAL"]:
            raise ProtocolError("the first planner call must return exactly one INIT_GOAL")

        planned_ids = set(state.goals)
        for action in actions:
            action_type = action["search_action"]
            if action_type == "STOP":
                continue
            queries = action.get("search_queries")
            if not isinstance(queries, list) or not queries or not all(
                isinstance(query, str) and query.strip() for query in queries
            ):
                raise ProtocolError(f"{action_type}.search_queries must be non-empty strings")
            if len(queries) > self.config.max_queries_per_action:
                raise ProtocolError(
                    f"{action_type} has more than "
                    f"{self.config.max_queries_per_action} search queries"
                )

            if action_type in {"INIT_GOAL", "EXPAND_GOAL", "REFINE_GOAL"}:
                goal_id = action.get("search_goal_id")
                goal_text = action.get("search_goal")
                dependencies = action.get("depends_on_goal_ids", [])
                if not isinstance(goal_id, str) or not goal_id:
                    raise ProtocolError(f"{action_type}.search_goal_id is required")
                if goal_id in planned_ids:
                    raise ProtocolError(f"duplicate search_goal_id: {goal_id}")
                if not isinstance(goal_text, str) or not goal_text.strip():
                    raise ProtocolError(f"{action_type}.search_goal is required")
                if not isinstance(dependencies, list) or any(
                    dependency not in planned_ids for dependency in dependencies
                ):
                    raise ProtocolError(
                        f"{action_type}.depends_on_goal_ids contains unknown goals"
                    )
                planned_ids.add(goal_id)

            if action_type in {"CONTINUE_SEARCH", "VERIFY_CONFLICT", "REFINE_GOAL"}:
                target_id = action.get("target_goal_id")
                if target_id not in state.goals:
                    raise ProtocolError(f"{action_type}.target_goal_id is unknown")
                if (
                    action_type in {"CONTINUE_SEARCH", "VERIFY_CONFLICT"}
                    and state.goals[target_id].status not in ["OPEN", "UNRESOLVED"]
                ):
                    raise ProtocolError(f"{action_type} requires an OPEN or UNRESOLVED target goal")
            if action_type == "VERIFY_CONFLICT":
                conflict_id = action.get("target_conflict_id")
                known_conflicts = {item["conflict_id"] for item in state.conflicts}
                if conflict_id not in known_conflicts:
                    raise ProtocolError("VERIFY_CONFLICT.target_conflict_id is unknown")

    def _apply_new_goals(
        self, actions: list[dict[str, Any]], state: SearchState, turn_index: int
    ) -> None:
        for action in actions:
            action_type = action["search_action"]
            if action_type not in {"INIT_GOAL", "EXPAND_GOAL", "REFINE_GOAL"}:
                continue
            goal = GoalState(
                search_goal_id=action["search_goal_id"],
                search_goal=action["search_goal"],
                depends_on_goal_ids=list(action.get("depends_on_goal_ids", [])),
                turn_ids=[turn_index],
            )
            state.goals[goal.search_goal_id] = goal
            self.logger.info(
                "search_goal_created: goal=%s action=%s",
                goal.search_goal_id,
                action_type,
                extra={
                    "event": "goal_created",
                    "run_id": state.run_id,
                    "turn_id": turn_index,
                    "search_goal_id": goal.search_goal_id,
                    "search_goal": goal.search_goal,
                    "search_action": action_type,
                    "depends_on_goal_ids": goal.depends_on_goal_ids,
                },
            )
            if action_type == "REFINE_GOAL":
                target = state.goals[action["target_goal_id"]]
                target.status = "SUPERSEDED"
                target.status_reason = f"Refined by {goal.search_goal_id}"

    def _effective_goal_id(self, action: Mapping[str, Any]) -> str:
        if action["search_action"] in {"CONTINUE_SEARCH", "VERIFY_CONFLICT"}:
            return str(action["target_goal_id"])
        return str(action["search_goal_id"])

    def _execute_action(
        self, action: dict[str, Any], state: SearchState, turn_index: int
    ) -> dict[str, Any]:
        goal_id = self._effective_goal_id(action)
        goal = state.goals[goal_id]
        self._stage = "web_search"
        if turn_index not in goal.turn_ids:
            goal.turn_ids.append(turn_index)

        queries = [query.strip() for query in action["search_queries"]]
        state.metrics.search_action_count += 1
        state.metrics.search_query_count += len(queries)
        search_calls: list[dict[str, Any]] = []
        search_errors: list[dict[str, str]] = []
        current_webs: list[dict[str, Any]] = []
        seen_web_ids: set[str] = set()
        for query in queries:
            goal.search_queries.append(query)
            search_started_at = time.perf_counter()
            self.logger.info(
                "web_search_started: goal=%s query=%s",
                goal_id,
                query,
                extra={
                    "event": "web_search_started",
                    "run_id": state.run_id,
                    "turn_id": turn_index,
                    "search_goal_id": goal_id,
                    "query": query,
                    "top_k": self.config.top_k,
                },
            )
            try:
                results = self.web_search(query, top_k=self.config.top_k)
                if not isinstance(results, list):
                    raise TypeError("web_search must return a list")
                state.metrics.web_result_count += len(results[: self.config.top_k])
                registered = self._register_webs(results, state)
                ids = [web["web_id"] for web in registered]
                search_calls.append({"query": query, "web_ids": ids})
                self.logger.info(
                    "web_search_completed: goal=%s results=%s duration_ms=%s",
                    goal_id,
                    len(registered),
                    round((time.perf_counter() - search_started_at) * 1000, 2),
                    extra={
                        "event": "web_search_completed",
                        "run_id": state.run_id,
                        "turn_id": turn_index,
                        "search_goal_id": goal_id,
                        "query": query,
                        "result_count": len(registered),
                        "web_ids": ids,
                        "duration_ms": round(
                            (time.perf_counter() - search_started_at) * 1000, 2
                        ),
                    },
                )
                for web in registered:
                    if web["web_id"] not in seen_web_ids:
                        current_webs.append(web)
                        seen_web_ids.add(web["web_id"])
                    if web["web_id"] not in goal.web_ids:
                        goal.web_ids.append(web["web_id"])
            except Exception as exc:  # Preserve partial trajectory when one query fails.
                search_errors.append(
                    {"query": query, "error": f"{type(exc).__name__}: {exc}"}
                )
                self.logger.warning(
                    "web_search_failed: goal=%s query=%s error=%s",
                    goal_id,
                    query,
                    exc,
                    exc_info=True,
                    extra={
                        "event": "web_search_failed",
                        "run_id": state.run_id,
                        "turn_id": turn_index,
                        "search_goal_id": goal_id,
                        "query": query,
                        "error_type": type(exc).__name__,
                        "duration_ms": round(
                            (time.perf_counter() - search_started_at) * 1000, 2
                        ),
                    },
                )

        self._stage = "evidence_processor"
        evidence_messages = self.context.evidence_messages(state, goal_id, current_webs)
        raw_evidence = self._call_model(evidence_messages, "evidence_processor")
        evidences, conflicts, updated_conflicts = self._normalize_evidence(
            raw_evidence,
            goal_id,
            state,
            allowed_web_ids={web["web_id"] for web in current_webs},
        )
        state.evidences.extend(evidences)
        state.conflicts.extend(conflicts)
        for evidence in evidences:
            goal.evidence_ids.append(evidence["evidence_id"])
        for conflict in conflicts:
            goal.conflict_ids.append(conflict["conflict_id"])
        self.logger.info(
            "evidence_processed: goal=%s evidences=%s conflicts=%s conflict_updates=%s webs=%s",
            goal_id,
            len(evidences),
            len(conflicts),
            len(updated_conflicts),
            len(current_webs),
            extra={
                "event": "evidence_processed",
                "run_id": state.run_id,
                "turn_id": turn_index,
                "search_goal_id": goal_id,
                "evidence_ids": [item["evidence_id"] for item in evidences],
                "conflict_ids": [item["conflict_id"] for item in conflicts],
                "updated_conflict_ids": [
                    item["conflict_id"] for item in updated_conflicts
                ],
                "web_ids": [item["web_id"] for item in current_webs],
                "search_error_count": len(search_errors),
            },
        )

        return {
            "search_action": copy.deepcopy(action),
            "search_goal_id": goal_id,
            "search_queries": queries,
            "search_calls": search_calls,
            "search_errors": search_errors,
            "webs": copy.deepcopy(current_webs),
            "evidence_processor": {
                "messages": evidence_messages,
                "response": raw_evidence,
            },
            "evidences": copy.deepcopy(evidences),
            "conflicts": copy.deepcopy(conflicts),
            "updated_conflicts": copy.deepcopy(updated_conflicts),
        }

    def _register_webs(
        self, results: list[dict[str, Any]], state: SearchState
    ) -> list[dict[str, Any]]:
        registered: list[dict[str, Any]] = []
        for result in results[: self.config.top_k]:
            if not isinstance(result, Mapping):
                raise TypeError("every web search result must be an object")
            source = dict(result)
            dedupe_key = str(
                source.get("url")
                or (source.get("title"), source.get("date"), source.get("content"))
            )
            web_id = self._web_key_to_id.get(dedupe_key)
            if web_id is None:
                web_id = str(len(state.webs) + 1)
                self._web_key_to_id[dedupe_key] = web_id
                source["source_id"] = source.pop("id", None)
                source["web_id"] = web_id
                state.webs.append(source)
            registered.append(state.webs[int(web_id) - 1])
        return registered

    def _normalize_evidence(
        self,
        response: Mapping[str, Any],
        goal_id: str,
        state: SearchState,
        allowed_web_ids: set[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        raw_evidences = response.get("evidences", [])
        raw_conflicts = response.get("conflicts", [])
        raw_conflict_updates = response.get("conflict_updates", [])
        if not isinstance(raw_evidences, list) or not all(
            isinstance(item, Mapping) for item in raw_evidences
        ):
            raise ProtocolError("evidence_processor.evidences must be an array")
        if not isinstance(raw_conflicts, list) or not all(
            isinstance(item, Mapping) for item in raw_conflicts
        ):
            raise ProtocolError("evidence_processor.conflicts must be an array")
        if not isinstance(raw_conflict_updates, list) or not all(
            isinstance(item, Mapping) for item in raw_conflict_updates
        ):
            raise ProtocolError("evidence_processor.conflict_updates must be an array")

        evidence_id_map: dict[str, str] = {}
        evidences: list[dict[str, Any]] = []
        for raw in raw_evidences:
            old_id = str(raw.get("evidence_id") or raw.get("id") or "")
            new_id = f"E{len(state.evidences) + len(evidences) + 1}"
            if old_id:
                evidence_id_map[old_id] = new_id
            evidence = copy.deepcopy(dict(raw))
            evidence.pop("id", None)
            evidence["evidence_id"] = new_id
            evidence["search_goal_id"] = goal_id
            quotes = evidence.get("quotes", [])
            if not isinstance(quotes, list) or not quotes:
                raise ProtocolError(f"{new_id}.quotes must be a non-empty array")
            for quote_group in quotes:
                if not isinstance(quote_group, dict):
                    raise ProtocolError(f"{new_id}.quotes items must be objects")
                quote_group["web_id"] = self._clean_web_id(quote_group.get("web_id"))
                if quote_group["web_id"] not in allowed_web_ids:
                    raise ProtocolError(
                        f"{new_id} references a web outside current_search_webs: "
                        f"{quote_group['web_id']}"
                    )
                quoted_text = quote_group.get("quotes")
                if not isinstance(quoted_text, list) or not all(
                    isinstance(text, str) and text for text in quoted_text
                ):
                    raise ProtocolError(f"{new_id}.quotes must contain non-empty strings")
            evidences.append(evidence)

        known_evidence_ids = {
            evidence["evidence_id"] for evidence in state.evidences + evidences
        }

        conflicts: list[dict[str, Any]] = []
        for raw in raw_conflicts:
            conflict = copy.deepcopy(dict(raw))
            conflict.pop("id", None)
            conflict["conflict_id"] = f"C{len(state.conflicts) + len(conflicts) + 1}"
            conflict["search_goal_id"] = goal_id
            # Set once at creation; the orchestrator's turn loop is the sole
            # owner of this field from here on (see _apply_conflict_status) —
            # the model never sets or reads it back as an input it controls.
            conflict["conflict_status"] = "UNCLASSIFIED"
            ids = conflict.get("conflict_evidence_ids", [])
            if not isinstance(ids, list):
                raise ProtocolError("conflict_evidence_ids must be an array")
            conflict["conflict_evidence_ids"] = [
                evidence_id_map.get(str(item), str(item)) for item in ids
            ]
            if any(
                item not in known_evidence_ids
                for item in conflict["conflict_evidence_ids"]
            ):
                raise ProtocolError("conflict references unknown evidence IDs")
            conflicts.append(conflict)

        # A conflict already tracked for this goal can gain new supporting
        # evidence without minting a duplicate conflict_id: the model names
        # the existing conflict via prior_conflicts and only supplies the new
        # evidence ids to attach. This mutates the matching entry already
        # sitting in ``state.conflicts`` in place, which is why merges must
        # target a conflict from a *prior* call, not one created just above.
        known_conflicts_for_goal = {
            conflict["conflict_id"]: conflict
            for conflict in state.conflicts
            if conflict.get("search_goal_id") == goal_id
        }
        updated_conflicts: list[dict[str, Any]] = []
        for raw in raw_conflict_updates:
            existing_id = str(raw.get("existing_conflict_id") or "")
            target = known_conflicts_for_goal.get(existing_id)
            if target is None:
                raise ProtocolError(
                    "conflict_updates references unknown existing_conflict_id: "
                    f"{existing_id}"
                )
            add_ids = raw.get("add_evidence_ids", [])
            if not isinstance(add_ids, list):
                raise ProtocolError(
                    "conflict_updates.add_evidence_ids must be an array"
                )
            resolved_add_ids = [
                evidence_id_map.get(str(item), str(item)) for item in add_ids
            ]
            if any(item not in known_evidence_ids for item in resolved_add_ids):
                raise ProtocolError(
                    "conflict_updates references unknown evidence IDs"
                )
            target["conflict_evidence_ids"] = list(
                dict.fromkeys(target["conflict_evidence_ids"] + resolved_add_ids)
            )
            updated_conflicts.append(copy.deepcopy(target))

        return evidences, conflicts, updated_conflicts

    @staticmethod
    def _clean_web_id(value: Any) -> str:
        text = str(value or "").strip()
        for prefix in ("网页[", "["):
            if text.startswith(prefix) and text.endswith("]"):
                return text[len(prefix) : -1]
        return text

    def _apply_state_updates(
        self, updates: list[dict[str, Any]], state: SearchState
    ) -> None:
        known_evidence = {item["evidence_id"] for item in state.evidences}
        known_conflicts = {
            item["conflict_id"]: item for item in state.conflicts
        }
        for update in updates:
            goal_id = update.get("search_goal_id")
            if goal_id not in state.goals:
                raise ProtocolError(f"state update references unknown goal: {goal_id}")
            goal = state.goals[str(goal_id)]
            if "depends_on_goal_ids" in update:
                ignored_dependencies = update.pop("depends_on_goal_ids")
                self.logger.warning(
                    "state_update_dependencies_ignored: goal=%s dependencies=%s",
                    goal_id,
                    ignored_dependencies,
                    extra={
                        "event": "state_update_dependencies_ignored",
                        "run_id": state.run_id,
                        "turn_id": self._active_turn.get("turn_id")
                        if self._active_turn
                        else None,
                        "search_goal_id": goal_id,
                        "ignored_depends_on_goal_ids": ignored_dependencies,
                        "preserved_depends_on_goal_ids": goal.depends_on_goal_ids,
                    },
                )
            status = update.get("status")
            if status not in GOAL_STATUSES:
                raise ProtocolError(f"invalid goal status: {status}")
            supporting = update.get("supporting_evidence_ids")
            legacy_supporting = update.get("supported_evidence_ids")
            if not supporting and legacy_supporting:
                supporting = legacy_supporting
            if supporting is None:
                supporting = []
            conflicting = update.get("conflicting_evidence_ids", [])
            if not isinstance(supporting, list) or any(
                not isinstance(item, str) or item not in known_evidence
                for item in supporting
            ):
                raise ProtocolError("state update references unknown evidence IDs")
            if not isinstance(conflicting, list):
                raise ProtocolError("conflicting_evidence_ids must be an array")

            # Canonically this field contains E* IDs. For compatibility with old
            # prompt examples, expand a C* ID to the evidences behind that conflict.
            normalized_conflicting: list[str] = []
            for item in conflicting:
                if not isinstance(item, str):
                    raise ProtocolError(
                        "conflicting_evidence_ids must contain strings"
                    )
                if item in known_evidence:
                    normalized_conflicting.append(item)
                    continue
                conflict = known_conflicts.get(item)
                if conflict is None:
                    raise ProtocolError(
                        "state update references unknown conflicting evidence IDs"
                    )
                conflict_evidence_ids = conflict.get("conflict_evidence_ids", [])
                normalized_conflicting.extend(
                    evidence_id
                    for evidence_id in conflict_evidence_ids
                    if evidence_id in known_evidence
                )
                self.logger.warning(
                    "conflict_id_expanded_to_evidence_ids: goal=%s conflict=%s",
                    goal_id,
                    item,
                    extra={
                        "event": "state_update_id_normalized",
                        "run_id": state.run_id,
                        "turn_id": self._active_turn.get("turn_id")
                        if self._active_turn
                        else None,
                        "search_goal_id": goal_id,
                        "input_conflict_id": item,
                        "output_evidence_ids": conflict_evidence_ids,
                    },
                )
            conflicting = list(dict.fromkeys(normalized_conflicting))

            # A planner may repeat an already-CLOSED, non-executed goal. Preserve
            # its validated provenance instead of replacing it with an empty list.
            if (
                status == "CLOSED"
                and not supporting
                and goal.status == "CLOSED"
                and goal.supporting_evidence_ids
            ):
                supporting = list(goal.supporting_evidence_ids)
                self.logger.warning(
                    "closed_goal_reused_existing_support: goal=%s evidence=%s",
                    goal_id,
                    supporting,
                    extra={
                        "event": "state_update_support_preserved",
                        "run_id": state.run_id,
                        "turn_id": self._active_turn.get("turn_id")
                        if self._active_turn
                        else None,
                        "search_goal_id": goal_id,
                        "supporting_evidence_ids": supporting,
                    },
                )

            update["supporting_evidence_ids"] = list(supporting)
            update["conflicting_evidence_ids"] = list(conflicting)
            if status == "CLOSED" and not supporting:
                self.logger.error(
                    "closed_goal_missing_supporting_evidence: goal=%s known=%s update=%s",
                    goal_id,
                    sorted(known_evidence),
                    update,
                    extra={
                        "event": "protocol_violation",
                        "run_id": state.run_id,
                        "turn_id": self._active_turn.get("turn_id")
                        if self._active_turn
                        else None,
                        "stage": self._stage,
                        "search_goal_id": goal_id,
                        "violation": "closed_goal_missing_supporting_evidence",
                        "known_evidence_ids": sorted(known_evidence),
                        "state_update": update,
                    },
                )
                raise ProtocolError(
                    f"CLOSED goal {goal_id} requires supporting evidence; "
                    f"known evidence IDs={sorted(known_evidence)}"
                )
            if status == "CLOSED" and conflicting:
                raise ProtocolError("CLOSED goals cannot have blocking conflicts")

            goal.status = str(status)
            goal.status_reason = str(update.get("status_reason", ""))
            goal.answer = str(update.get("answer", ""))
            goal.supported_statement = str(update.get("supported_statement", ""))
            goal.supporting_evidence_ids = list(supporting)
            goal.conflict_summary = str(update.get("conflict_summary", ""))
            goal.conflicting_evidence_ids = list(conflicting)
            goal.evidence_gap = str(update.get("evidence_gap", ""))
            self.logger.info(
                "search_goal_state_updated: goal=%s status=%s supporting=%s conflicts=%s",
                goal_id,
                status,
                list(supporting),
                list(conflicting),
                extra={
                    "event": "goal_state_updated",
                    "run_id": state.run_id,
                    "turn_id": self._active_turn.get("turn_id")
                    if self._active_turn
                    else None,
                    "search_goal_id": goal_id,
                    "status": status,
                    "supporting_evidence_ids": list(supporting),
                    "conflicting_evidence_ids": list(conflicting),
                    "evidence_gap": goal.evidence_gap,
                },
            )

    def _apply_conflict_status(
        self,
        state: SearchState,
        updated_goal_ids: set[str],
        next_actions: list[dict[str, Any]],
    ) -> None:
        """Label each touched goal's conflicts SOLVING or NON_BLOCKING.

        A conflict is SOLVING when the planner just scheduled a
        VERIFY_CONFLICT against it for the next turn — i.e. the planner
        judged it blocks this goal's closure and is actively working it.
        Every other conflict belonging to a goal touched this turn is
        NON_BLOCKING: the planner saw it and chose not to chase it, which is
        itself the planner's judgment that it does not block closure. This
        relabeling is re-evaluated every time a goal is touched, not a
        one-time assignment, and it is exposed back to the evidence
        processor (as ``prior_conflicts``) so repeated searches around an
        already-classified conflict do not keep minting duplicate records.
        """
        solving_conflict_ids = {
            str(action.get("target_conflict_id"))
            for action in next_actions
            if isinstance(action, Mapping)
            and action.get("search_action") == "VERIFY_CONFLICT"
        }
        for conflict in state.conflicts:
            if conflict.get("search_goal_id") not in updated_goal_ids:
                continue
            previous_status = conflict.get("conflict_status")
            new_status = (
                "SOLVING"
                if conflict.get("conflict_id") in solving_conflict_ids
                else "NON_BLOCKING"
            )
            conflict["conflict_status"] = new_status
            if previous_status != new_status:
                self.logger.info(
                    "conflict_status_applied: conflict=%s goal=%s status=%s",
                    conflict.get("conflict_id"),
                    conflict.get("search_goal_id"),
                    new_status,
                    extra={
                        "event": "conflict_status_applied",
                        "run_id": state.run_id,
                        "turn_id": self._active_turn.get("turn_id")
                        if self._active_turn
                        else None,
                        "conflict_id": conflict.get("conflict_id"),
                        "search_goal_id": conflict.get("search_goal_id"),
                        "previous_status": previous_status,
                        "new_status": new_status,
                    },
                )

    @staticmethod
    def _is_stop(actions: list[dict[str, Any]]) -> bool:
        return len(actions) == 1 and actions[0].get("search_action") == "STOP"

    def _checkpoint(self, state: SearchState) -> None:
        state.metrics.unique_web_count = len(state.webs)
        if self.output_path:
            save_json_atomic(
                self.output_path,
                {
                    "trajectory": state.to_dict(),
                    "answer_agent_handoff": state.answer_handoff(),
                },
            )
            self.logger.info(
                "trajectory_checkpoint_saved: status=%s path=%s",
                state.run_status,
                self.output_path,
                extra={
                    "event": "checkpoint_saved",
                    "run_id": state.run_id,
                    "turn_id": self._active_turn.get("turn_id")
                    if self._active_turn
                    else None,
                    "run_status": state.run_status,
                    "output_path": str(self.output_path.resolve()),
                    "turn_count": len(state.turns),
                    "goal_count": len(state.goals),
                    "evidence_count": len(state.evidences),
                    "conflict_count": len(state.conflicts),
                    "web_count": len(state.webs),
                    "metrics": state.metrics.to_dict(),
                },
            )

    def _finish_metrics(
        self, state: SearchState, termination_type: str, final_action: str
    ) -> None:
        state.metrics.unique_web_count = len(state.webs)
        state.metrics.finish(termination_type, final_action)
        self.logger.info(
            "run_metrics: turns=%s search_turns=%s termination=%s final_action=%s",
            state.metrics.total_turns,
            state.metrics.search_turns,
            termination_type,
            final_action,
            extra={
                "event": "run_metrics",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "metrics": state.metrics.to_dict(),
            },
        )
