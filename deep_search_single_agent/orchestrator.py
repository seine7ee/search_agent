"""Orchestration layer for single-agent deep-search trajectory synthesis.

Unlike ``deep_search_claude`` (planner + evidence-processor, two model calls
per turn), this scheme drives one teacher-model call per round. Round 1 only
creates the first persistent search goal (``CREATE_GOAL``) and plans its
queries; there is no observation yet, so nothing is extracted. Every round
from 2 onward is handed the webs collected for whatever the *previous*
round's action targeted, and in one call extracts evidence, identifies
conflicts, updates that one goal's state, and decides the next action
(``SEARCH``, ``CREATE_GOAL``, or ``STOP``).
"""

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
    from .models import (
        GOAL_STATUSES,
        MAX_DERIVATION_DEPTH,
        SEARCH_ACTIONS,
        GoalState,
        SearchState,
    )
    from .storage import save_json_atomic
except ImportError:  # pragma: no cover - used only by direct script execution.
    from context import ContextAssembler
    from models import (
        GOAL_STATUSES,
        MAX_DERIVATION_DEPTH,
        SEARCH_ACTIONS,
        GoalState,
        SearchState,
    )
    from storage import save_json_atomic

ModelRequester = Callable[[list[dict[str, str]]], Mapping[str, Any]]
WebSearcher = Callable[..., list[dict[str, Any]]]


class ProtocolError(ValueError):
    """Raised when the teacher model violates the structured protocol."""


@dataclass(frozen=True)
class OrchestratorConfig:
    max_rounds: int = 10
    top_k: int = 10
    max_queries_per_action: int = 5
    max_web_content_chars: int = 6000
    model_max_attempts: int = 3
    model_retry_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_rounds <= 1:
            raise ValueError("max_rounds must be greater than 1 (round 1 only creates a goal)")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.max_queries_per_action <= 0:
            raise ValueError("max_queries_per_action must be positive")
        if self.model_max_attempts <= 0:
            raise ValueError("model_max_attempts must be positive")
        if self.model_retry_delay_seconds < 0:
            raise ValueError("model_retry_delay_seconds cannot be negative")


class SingleAgentOrchestrator:
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
        self.logger = logger or logging.getLogger("deep_search_single_agent.orchestrator")
        self._active_state: SearchState | None = None
        self._active_round: dict[str, Any] | None = None
        self._last_model_call: dict[str, Any] | None = None
        self._stage = "idle"

    def run(self, user_query: str, current_date: str | None = None) -> SearchState:
        """Run synthesis and persist diagnostic state even when the run fails."""
        self._active_state = None
        self._active_round = None
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
                if self._active_round is not None:
                    active_round_id = self._active_round.get("round_id")
                    if not state.rounds or state.rounds[-1].get("round_id") != active_round_id:
                        failed_round = copy.deepcopy(self._active_round)
                        failed_round["failure"] = failure
                        state.rounds.append(failed_round)
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
                "max_rounds": self.config.max_rounds,
                "top_k": self.config.top_k,
                "output_path": str(self.output_path) if self.output_path else None,
            },
        )

        pending = self._run_round1(state)
        state.metrics.total_rounds = 1

        for round_id in range(2, self.config.max_rounds + 1):
            self._stage = "turn_started"
            state.metrics.total_rounds = round_id
            state.metrics.search_rounds += 1
            round_record: dict[str, Any] = {"round_id": round_id, "kind": "turn"}
            self._active_round = round_record
            self.logger.info(
                "round_started: round=%s target_goal=%s",
                round_id,
                pending["goal_id"],
                extra={
                    "event": "round_started",
                    "run_id": state.run_id,
                    "round_id": round_id,
                    "target_goal_id": pending["goal_id"],
                },
            )

            messages = self.context.turn_messages(state, pending)
            response = self._call_model(messages, "single_agent")
            round_record["model_call"] = {"messages": messages, "response": response}
            round_record["target_goal_id"] = pending["goal_id"]
            round_record["target_goal_text"] = state.goals[pending["goal_id"]].search_goal
            conflict = pending.get("conflict")
            round_record["target_conflict_id"] = (
                conflict.get("conflict_id") if isinstance(conflict, Mapping) else None
            )
            round_record["search_queries"] = pending.get("search_queries", [])

            self._stage = "extract_turn_response"
            raw_evidences, raw_conflicts, raw_update, raw_action = self._extract_turn_response(
                response
            )

            self._stage = "normalize_evidence"
            allowed_web_ids = {web["web_id"] for web in pending.get("webs", [])}
            evidences, evidence_id_map = self._normalize_evidences(
                raw_evidences, state, allowed_web_ids
            )
            conflicts = self._normalize_conflicts(
                raw_conflicts, state, pending["goal_id"], evidences, evidence_id_map
            )
            state.evidences.extend(evidences)
            state.conflicts.extend(conflicts)
            goal = state.goals[pending["goal_id"]]
            goal.evidence_ids.extend(e["evidence_id"] for e in evidences)
            goal.conflict_ids.extend(c["conflict_id"] for c in conflicts)
            round_record["evidences"] = evidences
            round_record["conflicts"] = conflicts

            self._stage = "apply_state_update"
            known_evidence_ids = {e["evidence_id"] for e in state.evidences}
            known_conflict_ids = {c["conflict_id"] for c in state.conflicts}
            status = self._apply_state_update(
                raw_update,
                state,
                pending["goal_id"],
                known_evidence_ids,
                known_conflict_ids,
                evidence_id_map,
            )
            round_record["state_update"] = state.goals[pending["goal_id"]].to_dict()

            self.logger.info(
                "round_state_updated: round=%s goal=%s status=%s evidences=%s conflicts=%s",
                round_id,
                pending["goal_id"],
                status,
                len(evidences),
                len(conflicts),
                extra={
                    "event": "round_state_updated",
                    "run_id": state.run_id,
                    "round_id": round_id,
                    "search_goal_id": pending["goal_id"],
                    "status": status,
                    "evidence_ids": [e["evidence_id"] for e in evidences],
                    "conflict_ids": [c["conflict_id"] for c in conflicts],
                },
            )

            self._stage = "validate_action"
            search_action = self._validate_and_apply_action(
                raw_action, state, pending["goal_id"], status, known_conflict_ids
            )
            round_record["action"] = copy.deepcopy(dict(raw_action))
            state.rounds.append(round_record)
            self._active_round = None
            self._checkpoint(state)

            if search_action == "STOP":
                state.stop_reason = str(
                    raw_action.get("action_reason", "Search agent requested STOP")
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
                        "round_id": round_id,
                        "stop_reason": state.stop_reason,
                        "goal_count": len(state.goals),
                        "evidence_count": len(state.evidences),
                    },
                )
                return state

            state.metrics.last_model_action = search_action
            if search_action == "CREATE_GOAL":
                self._register_new_goal(dict(raw_action), state, round_id)

            self._stage = "execute_action"
            pending = self._execute_action(dict(raw_action), state, round_id)

        state.run_status = "MAX_ROUNDS_REACHED"
        state.stop_reason = f"Reached max_rounds={self.config.max_rounds}"
        self._finish_metrics(state, "MAX_ROUNDS", "MAX_ROUNDS_REACHED")
        self._checkpoint(state)
        self.logger.warning(
            "deep_search_max_rounds_reached",
            extra={
                "event": "max_rounds_reached",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "max_rounds": self.config.max_rounds,
            },
        )
        return state

    # ------------------------------------------------------------------
    # Round 1: create the first goal.
    # ------------------------------------------------------------------

    def _run_round1(self, state: SearchState) -> dict[str, Any]:
        self._stage = "round1_create_goal"
        round_record: dict[str, Any] = {"round_id": 1, "kind": "init"}
        self._active_round = round_record
        messages = self.context.initial_messages(state.user_query, state.current_date)
        response = self._call_model(messages, "single_agent")
        round_record["model_call"] = {"messages": messages, "response": response}

        action = response.get("action")
        if not isinstance(action, Mapping):
            raise ProtocolError("round 1 response must contain an 'action' object")
        action = dict(action)
        if action.get("search_action") != "CREATE_GOAL":
            raise ProtocolError("round 1 must output a CREATE_GOAL action")
        if action.get("search_goal_id") != "G1":
            raise ProtocolError("round 1 CREATE_GOAL.search_goal_id must be 'G1'")
        if not isinstance(action.get("search_goal"), str) or not action["search_goal"].strip():
            raise ProtocolError("round 1 CREATE_GOAL.search_goal is required")
        if action.get("depends_on_goal_ids") not in (None, []):
            raise ProtocolError("round 1 CREATE_GOAL.depends_on_goal_ids must be empty")
        if action.get("supersedes_goal_id") is not None:
            raise ProtocolError("round 1 CREATE_GOAL.supersedes_goal_id must be null")
        queries = action.get("search_queries")
        if not isinstance(queries, list) or not queries or not all(
            isinstance(q, str) and q.strip() for q in queries
        ):
            raise ProtocolError("round 1 CREATE_GOAL.search_queries must be non-empty strings")
        if len(queries) > self.config.max_queries_per_action:
            raise ProtocolError("round 1 CREATE_GOAL has too many search_queries")

        goal = GoalState(search_goal_id="G1", search_goal=action["search_goal"], round_ids=[1])
        state.goals["G1"] = goal
        state.metrics.goal_created_count += 1
        self.logger.info(
            "search_goal_created: goal=G1 round=1",
            extra={
                "event": "goal_created",
                "run_id": state.run_id,
                "round_id": 1,
                "search_goal_id": "G1",
                "search_goal": goal.search_goal,
            },
        )

        self._stage = "round1_execute_search"
        pending = self._execute_action(action, state, round_id=1)
        round_record["action"] = copy.deepcopy(action)
        round_record["execution"] = copy.deepcopy(pending)
        state.rounds.append(round_record)
        self._active_round = None
        self._checkpoint(state)
        return pending

    # ------------------------------------------------------------------
    # Model calls
    # ------------------------------------------------------------------

    def _call_model(
        self, messages: list[dict[str, str]], agent_name: str
    ) -> dict[str, Any]:
        state = self._active_state
        call_record: dict[str, Any] = {
            "agent": agent_name,
            "stage": self._stage,
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
                    "round_id": self._active_round.get("round_id")
                    if self._active_round
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
                        "round_id": self._active_round.get("round_id")
                        if self._active_round
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
            call_record["attempts"].append({"attempt": attempt, "status": "SUCCEEDED"})
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
                    "round_id": self._active_round.get("round_id")
                    if self._active_round
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
                    "round_id": self._active_round.get("round_id")
                    if self._active_round
                    else None,
                    "agent": agent_name,
                    "stage": self._stage,
                    "attempt": attempt,
                    "model_response": normalized,
                },
            )
            return normalized

        raise RuntimeError("model retry loop exited unexpectedly")  # pragma: no cover

    # ------------------------------------------------------------------
    # Response extraction / normalization
    # ------------------------------------------------------------------

    def _extract_turn_response(
        self, response: Mapping[str, Any]
    ) -> tuple[list[Any], list[Any], Mapping[str, Any], Mapping[str, Any]]:
        evidences = response.get("evidences")
        conflicts = response.get("conflicts")
        state_update = response.get("search_state_update")
        action = response.get("action")
        if not isinstance(evidences, list):
            raise ProtocolError("evidences must be an array")
        if not isinstance(conflicts, list):
            raise ProtocolError("conflicts must be an array")
        if not isinstance(state_update, Mapping):
            raise ProtocolError("search_state_update must be an object")
        if not isinstance(action, Mapping):
            raise ProtocolError("action must be an object")
        return list(evidences), list(conflicts), dict(state_update), dict(action)

    def _normalize_evidences(
        self,
        raw_evidences: list[Any],
        state: SearchState,
        allowed_web_ids: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        evidences: list[dict[str, Any]] = []
        id_map: dict[str, str] = {}
        for raw in raw_evidences:
            if not isinstance(raw, Mapping):
                raise ProtocolError("every evidence must be an object")
            old_id = str(raw.get("evidence_id") or "")
            new_id = f"E{len(state.evidences) + len(evidences) + 1}"
            if old_id:
                id_map[old_id] = new_id
            evidence = copy.deepcopy(dict(raw))
            evidence["evidence_id"] = new_id
            statement = evidence.get("statement")
            if not isinstance(statement, str) or not statement.strip():
                raise ProtocolError(f"{new_id}.statement is required")
            quotes = evidence.get("quotes")
            if not isinstance(quotes, list) or not quotes:
                raise ProtocolError(f"{new_id}.quotes must be a non-empty array")
            for quote_group in quotes:
                if not isinstance(quote_group, Mapping):
                    raise ProtocolError(f"{new_id}.quotes items must be objects")
                quote_group = dict(quote_group)
                web_id = self._clean_web_id(quote_group.get("web_id"))
                if web_id not in allowed_web_ids:
                    raise ProtocolError(
                        f"{new_id} references a web outside current turn_webs: {web_id}"
                    )
                quote_group["web_id"] = web_id
                texts = quote_group.get("quotes")
                if not isinstance(texts, list) or not all(
                    isinstance(text, str) and text for text in texts
                ):
                    raise ProtocolError(f"{new_id}.quotes must contain non-empty strings")
            evidence["quotes"] = [
                {**dict(group), "web_id": self._clean_web_id(dict(group).get("web_id"))}
                for group in quotes
            ]
            evidences.append(evidence)
        return evidences, id_map

    def _normalize_conflicts(
        self,
        raw_conflicts: list[Any],
        state: SearchState,
        goal_id: str,
        new_evidences: list[dict[str, Any]],
        evidence_id_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        known_evidence_ids = {
            evidence["evidence_id"] for evidence in state.evidences + new_evidences
        }
        conflicts: list[dict[str, Any]] = []
        for raw in raw_conflicts:
            if not isinstance(raw, Mapping):
                raise ProtocolError("every conflict must be an object")
            conflict = copy.deepcopy(dict(raw))
            new_id = f"C{len(state.conflicts) + len(conflicts) + 1}"
            conflict["conflict_id"] = new_id
            conflict["search_goal_id"] = goal_id
            if not isinstance(conflict.get("conflict_object"), str) or not conflict[
                "conflict_object"
            ].strip():
                raise ProtocolError(f"{new_id}.conflict_object is required")
            if not isinstance(conflict.get("conflict_statement"), str) or not conflict[
                "conflict_statement"
            ].strip():
                raise ProtocolError(f"{new_id}.conflict_statement is required")
            ids = conflict.get("conflicted_evidence_ids", [])
            if not isinstance(ids, list) or not ids:
                raise ProtocolError(f"{new_id}.conflicted_evidence_ids must be non-empty")
            resolved_ids = [evidence_id_map.get(str(item), str(item)) for item in ids]
            if any(item not in known_evidence_ids for item in resolved_ids):
                raise ProtocolError(f"{new_id} references unknown evidence ids")
            conflict["conflicted_evidence_ids"] = resolved_ids
            conflicts.append(conflict)
        return conflicts

    @staticmethod
    def _clean_web_id(value: Any) -> str:
        text = str(value or "").strip()
        for prefix in ("网页[", "["):
            if text.startswith(prefix) and text.endswith("]"):
                return text[len(prefix) : -1]
        return text

    # ------------------------------------------------------------------
    # State-update / action validation
    # ------------------------------------------------------------------

    def _apply_state_update(
        self,
        raw_update: Mapping[str, Any],
        state: SearchState,
        goal_id: str,
        known_evidence_ids: set[str],
        known_conflict_ids: set[str],
        evidence_id_map: dict[str, str],
    ) -> str:
        if str(raw_update.get("search_goal_id")) != goal_id:
            raise ProtocolError(
                f"search_state_update.search_goal_id must equal the turn goal ({goal_id})"
            )
        goal = state.goals[goal_id]
        status = raw_update.get("status")
        if status not in GOAL_STATUSES:
            raise ProtocolError(f"invalid goal status: {status}")

        supported = raw_update.get("supported_evidence_ids", [])
        if not isinstance(supported, list) or any(
            not isinstance(item, str) for item in supported
        ):
            raise ProtocolError("supported_evidence_ids must be a list of strings")
        supported = [evidence_id_map.get(item, item) for item in supported]
        if any(item not in known_evidence_ids for item in supported):
            raise ProtocolError("supported_evidence_ids references unknown evidence")

        blocked = raw_update.get("blocked_conflict_ids", [])
        if not isinstance(blocked, list) or any(
            not isinstance(item, str) for item in blocked
        ):
            raise ProtocolError("blocked_conflict_ids must be a list of strings")
        if any(item not in known_conflict_ids for item in blocked):
            raise ProtocolError("blocked_conflict_ids references unknown conflict")

        if status in {"CLOSED", "REFUTED"}:
            if not supported:
                raise ProtocolError(f"{status} goal {goal_id} requires supported_evidence_ids")
            if blocked:
                raise ProtocolError(f"{status} goal {goal_id} cannot have blocked_conflict_ids")
        if status == "OPEN" and goal.search_round_count >= goal.max_searches:
            raise ProtocolError(
                f"goal {goal_id} exhausted its search budget "
                f"({goal.search_round_count}/{goal.max_searches}) and cannot remain OPEN"
            )
        if status == "UNRESOLVED" and goal.search_round_count < goal.max_searches:
            raise ProtocolError(
                f"goal {goal_id} marked UNRESOLVED before exhausting its search budget "
                f"({goal.search_round_count}/{goal.max_searches})"
            )

        goal.status = str(status)
        goal.supported_evidence_ids = list(supported)
        goal.supported_statement = str(raw_update.get("supported_statement", ""))
        goal.blocked_conflict_ids = list(blocked)
        goal.info_gap = str(raw_update.get("info_gap", ""))
        return str(status)

    def _validate_and_apply_action(
        self,
        action: Mapping[str, Any],
        state: SearchState,
        goal_id: str,
        status: str,
        known_conflict_ids: set[str],
    ) -> str:
        search_action = action.get("search_action")
        if search_action not in SEARCH_ACTIONS:
            raise ProtocolError(f"unknown search_action: {search_action}")

        if status == "SUPERSEDED":
            if search_action != "CREATE_GOAL" or action.get("supersedes_goal_id") != goal_id:
                raise ProtocolError(
                    f"goal {goal_id} marked SUPERSEDED but the action does not CREATE_GOAL "
                    f"with supersedes_goal_id={goal_id}"
                )

        if search_action == "STOP":
            open_goals = [
                g.search_goal_id for g in state.goals.values() if g.status == "OPEN"
            ]
            if open_goals:
                raise ProtocolError(f"STOP is not allowed while goals are OPEN: {open_goals}")
            return "STOP"

        queries = action.get("search_queries")
        if not isinstance(queries, list) or not queries or not all(
            isinstance(query, str) and query.strip() for query in queries
        ):
            raise ProtocolError(f"{search_action}.search_queries must be non-empty strings")
        if len(queries) > self.config.max_queries_per_action:
            raise ProtocolError(
                f"{search_action} has more than "
                f"{self.config.max_queries_per_action} search queries"
            )

        if search_action == "SEARCH":
            target_type = action.get("type")
            target_id = action.get("target_id")
            if target_type not in {"goal", "conflict"}:
                raise ProtocolError("SEARCH.type must be 'goal' or 'conflict'")
            if target_type == "goal":
                target_goal = state.goals.get(str(target_id))
                if target_goal is None or target_goal.status != "OPEN":
                    raise ProtocolError(
                        f"SEARCH.target_id must reference an OPEN goal: {target_id}"
                    )
            else:
                if str(target_id) not in known_conflict_ids:
                    raise ProtocolError(
                        f"SEARCH.target_id must reference a known conflict: {target_id}"
                    )
                conflict = self._conflict_by_id(state, str(target_id))
                owner = state.goals.get(conflict.get("search_goal_id"))
                if owner is None or owner.status != "OPEN":
                    raise ProtocolError(
                        "SEARCH type=conflict must target a conflict owned by an OPEN goal"
                    )
        elif search_action == "CREATE_GOAL":
            new_goal_id = action.get("search_goal_id")
            new_goal_text = action.get("search_goal")
            if not isinstance(new_goal_id, str) or not new_goal_id or new_goal_id in state.goals:
                raise ProtocolError("CREATE_GOAL.search_goal_id must be new and non-empty")
            if not isinstance(new_goal_text, str) or not new_goal_text.strip():
                raise ProtocolError("CREATE_GOAL.search_goal is required")
            dependencies = action.get("depends_on_goal_ids", []) or []
            if not isinstance(dependencies, list):
                raise ProtocolError("depends_on_goal_ids must be an array")
            for dependency in dependencies:
                dependency_goal = state.goals.get(dependency)
                if dependency_goal is None or dependency_goal.status != "CLOSED":
                    raise ProtocolError(
                        f"depends_on_goal_ids must reference CLOSED goals: {dependency}"
                    )
            supersedes = action.get("supersedes_goal_id")
            if supersedes is not None:
                parent = state.goals.get(supersedes)
                if parent is None:
                    raise ProtocolError(
                        f"supersedes_goal_id references unknown goal: {supersedes}"
                    )
                if parent.status != "SUPERSEDED":
                    raise ProtocolError(
                        "supersedes_goal_id target must be marked SUPERSEDED this round"
                    )
                if parent.derivation_depth >= MAX_DERIVATION_DEPTH:
                    raise ProtocolError(
                        f"goal {supersedes} is already at the max derivation depth"
                    )
        return str(search_action)

    def _conflict_by_id(self, state: SearchState, conflict_id: str) -> dict[str, Any]:
        for conflict in state.conflicts:
            if conflict.get("conflict_id") == conflict_id:
                return conflict
        raise ProtocolError(f"unknown conflict_id: {conflict_id}")

    def _register_new_goal(
        self, action: dict[str, Any], state: SearchState, round_id: int
    ) -> GoalState:
        goal_id = str(action["search_goal_id"])
        supersedes = action.get("supersedes_goal_id")
        derivation_depth = 0
        if supersedes:
            parent = state.goals[str(supersedes)]
            derivation_depth = parent.derivation_depth + 1
            parent.superseded_by = goal_id
        goal = GoalState(
            search_goal_id=goal_id,
            search_goal=str(action["search_goal"]),
            depends_on_goal_ids=list(action.get("depends_on_goal_ids", []) or []),
            supersedes_goal_id=str(supersedes) if supersedes else None,
            derivation_depth=derivation_depth,
            round_ids=[round_id],
        )
        state.goals[goal_id] = goal
        state.metrics.goal_created_count += 1
        self.logger.info(
            "search_goal_created: goal=%s round=%s supersedes=%s depth=%s",
            goal_id,
            round_id,
            supersedes,
            derivation_depth,
            extra={
                "event": "goal_created",
                "run_id": state.run_id,
                "round_id": round_id,
                "search_goal_id": goal_id,
                "search_goal": goal.search_goal,
                "supersedes_goal_id": goal.supersedes_goal_id,
                "derivation_depth": derivation_depth,
                "depends_on_goal_ids": goal.depends_on_goal_ids,
            },
        )
        return goal

    # ------------------------------------------------------------------
    # Web search execution
    # ------------------------------------------------------------------

    def _execute_action(
        self, action: dict[str, Any], state: SearchState, round_id: int
    ) -> dict[str, Any]:
        search_action = action["search_action"]
        conflict: dict[str, Any] | None = None
        search_focus: str | None = None
        if search_action == "CREATE_GOAL":
            goal_id = str(action["search_goal_id"])
        else:  # SEARCH
            target_type = action["type"]
            search_focus = action.get("search_focus")
            if target_type == "goal":
                goal_id = str(action["target_id"])
            else:
                conflict = self._conflict_by_id(state, str(action["target_id"]))
                goal_id = str(conflict["search_goal_id"])
                state.metrics.conflict_search_count += 1

        goal = state.goals[goal_id]
        goal.search_round_count += 1
        if round_id not in goal.round_ids:
            goal.round_ids.append(round_id)

        queries = [query.strip() for query in action["search_queries"]]
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
                    "round_id": round_id,
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
                        "round_id": round_id,
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
                        "round_id": round_id,
                        "search_goal_id": goal_id,
                        "query": query,
                        "error_type": type(exc).__name__,
                        "duration_ms": round(
                            (time.perf_counter() - search_started_at) * 1000, 2
                        ),
                    },
                )

        return {
            "round_id": round_id,
            "goal_id": goal_id,
            "conflict": copy.deepcopy(conflict) if conflict else None,
            "search_focus": search_focus,
            "search_queries": queries,
            "search_calls": search_calls,
            "search_errors": search_errors,
            "webs": current_webs,
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

    # ------------------------------------------------------------------
    # Persistence / metrics
    # ------------------------------------------------------------------

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
                    "round_id": self._active_round.get("round_id")
                    if self._active_round
                    else None,
                    "run_status": state.run_status,
                    "output_path": str(self.output_path.resolve()),
                    "round_count": len(state.rounds),
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
            "run_metrics: rounds=%s search_rounds=%s termination=%s final_action=%s",
            state.metrics.total_rounds,
            state.metrics.search_rounds,
            termination_type,
            final_action,
            extra={
                "event": "run_metrics",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "metrics": state.metrics.to_dict(),
            },
        )
