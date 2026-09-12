"""Single-agent ReAct orchestration for scheme-B trajectory synthesis."""

from __future__ import annotations

import copy
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from deep_search.storage import save_json_atomic

from .context import BaselineContextAssembler
from .models import BaselineState

ModelRequester = Callable[[list[dict[str, str]]], Mapping[str, Any]]
WebSearcher = Callable[..., list[dict[str, Any]]]


class BaselineProtocolError(ValueError):
    """Raised when the teacher returns an invalid SEARCH/STOP decision."""


@dataclass(frozen=True)
class BaselineConfig:
    max_turns: int = 8
    top_k: int = 10
    max_search_actions_per_turn: int = 3
    max_queries_per_action: int = 5
    max_web_content_chars: int = 6000

    def __post_init__(self) -> None:
        for name in (
            "max_turns",
            "top_k",
            "max_search_actions_per_turn",
            "max_queries_per_action",
            "max_web_content_chars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


class BaselineOrchestrator:
    def __init__(
        self,
        request_model: ModelRequester,
        web_search: WebSearcher,
        *,
        config: BaselineConfig | None = None,
        output_path: str | Path | None = None,
        logger: logging.Logger | None = None,
    ):
        self.request_model = request_model
        self.web_search = web_search
        self.config = config or BaselineConfig()
        self.output_path = Path(output_path) if output_path else None
        self.context = BaselineContextAssembler(self.config.max_web_content_chars)
        self.logger = logger or logging.getLogger("deep_search_baseline.orchestrator")
        self._web_key_to_id: dict[str, str] = {}
        self._active_state: BaselineState | None = None
        self._active_turn: dict[str, Any] | None = None
        self._last_model_call: dict[str, Any] | None = None
        self._stage = "idle"

    def run(self, user_query: str, current_date: str | None = None) -> BaselineState:
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
                            "scheme": state.metrics.scheme,
                        },
                    )
            self.logger.exception(
                "baseline_run_failed: stage=%s error=%s",
                self._stage,
                exc,
                extra={
                    "event": "run_failed",
                    "run_id": state.run_id if state else None,
                    "scheme": "scheme_b_baseline",
                    "stage": self._stage,
                    "error_type": type(exc).__name__,
                },
            )
            raise

    def _run_impl(
        self, user_query: str, current_date: str | None = None
    ) -> BaselineState:
        query = user_query.strip()
        if not query:
            raise ValueError("user_query cannot be empty")
        state = BaselineState(
            user_query=query,
            current_date=current_date or date.today().isoformat(),
        )
        self._active_state = state
        self._web_key_to_id = {}
        self.logger.info(
            "baseline_run_started",
            extra={
                "event": "run_started",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "user_query": state.user_query,
                "max_turns": self.config.max_turns,
                "top_k": self.config.top_k,
                "output_path": str(self.output_path) if self.output_path else None,
            },
        )

        for turn_index in range(1, self.config.max_turns + 1):
            self._stage = "teacher_decision"
            state.metrics.total_turns = turn_index
            messages = self.context.messages(state)
            response = self._call_model(messages)
            decision, actions = self._parse_decision(response)
            state.metrics.last_model_action = decision
            turn: dict[str, Any] = {
                "turn_id": turn_index,
                "teacher": {"messages": messages, "response": response},
                "decision": decision,
            }
            self._active_turn = turn
            self.logger.info(
                "baseline_turn_decision: turn=%s decision=%s",
                turn_index,
                decision,
                extra={
                    "event": "turn_decision",
                    "run_id": state.run_id,
                    "scheme": state.metrics.scheme,
                    "turn_id": turn_index,
                    "decision": decision,
                },
            )

            if decision == "STOP":
                turn["action_reason"] = response["action_reason"]
                state.turns.append(turn)
                self._active_turn = None
                state.run_status = "COMPLETED"
                state.stop_reason = response["action_reason"]
                self._finish_metrics(state, "MODEL_STOP", "STOP")
                self._checkpoint(state)
                return state

            state.metrics.search_turns += 1
            executions = [
                self._execute_search_action(action, state, turn_index)
                for action in actions
            ]
            turn["search_actions"] = executions
            state.turns.append(turn)
            self._active_turn = None
            self._checkpoint(state)

        state.run_status = "MAX_TURNS_REACHED"
        state.stop_reason = f"Reached max_turns={self.config.max_turns}"
        self._finish_metrics(state, "MAX_TURNS", "MAX_TURNS_REACHED")
        self._checkpoint(state)
        self.logger.warning(
            "baseline_max_turns_reached",
            extra={
                "event": "max_turns_reached",
                "run_id": state.run_id,
                "scheme": state.metrics.scheme,
                "metrics": state.metrics.to_dict(),
            },
        )
        return state

    def _call_model(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        state = self._active_state
        if state is not None:
            state.metrics.model_call_count += 1
        call_record: dict[str, Any] = {
            "agent": "deep_search_baseline",
            "stage": self._stage,
            "messages": copy.deepcopy(messages),
            "attempts": [],
        }
        self._last_model_call = call_record
        started_at = time.perf_counter()
        self.logger.info(
            "model_call_started: agent=deep_search_baseline",
            extra={
                "event": "model_call_started",
                "run_id": state.run_id if state else None,
                "scheme": "scheme_b_baseline",
                "turn_id": state.metrics.total_turns if state else None,
                "agent": "deep_search_baseline",
                "prompt_chars": sum(len(item["content"]) for item in messages),
            },
        )
        try:
            response = self.request_model(messages)
            if not isinstance(response, Mapping):
                raise BaselineProtocolError("teacher must return a JSON object")
        except Exception as exc:
            call_record["status"] = "FAILED"
            call_record["attempt"] = 1
            call_record["attempt_count"] = 1
            call_record["error_type"] = type(exc).__name__
            call_record["error"] = str(exc)
            call_record["attempts"].append(
                {
                    "attempt": 1,
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            raise
        normalized = copy.deepcopy(dict(response))
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        call_record["status"] = "SUCCEEDED"
        call_record["attempt"] = 1
        call_record["attempt_count"] = 1
        call_record["response"] = copy.deepcopy(normalized)
        call_record["attempts"].append(
            {"attempt": 1, "status": "SUCCEEDED"}
        )
        self.logger.info(
            "model_call_completed: agent=deep_search_baseline duration_ms=%s",
            elapsed_ms,
            extra={
                "event": "model_call_completed",
                "run_id": state.run_id if state else None,
                "scheme": "scheme_b_baseline",
                "turn_id": state.metrics.total_turns if state else None,
                "agent": "deep_search_baseline",
                "duration_ms": elapsed_ms,
                "response_keys": list(normalized),
            },
        )
        self.logger.debug(
            "model_response",
            extra={
                "event": "model_response",
                "run_id": state.run_id if state else None,
                "scheme": "scheme_b_baseline",
                "turn_id": state.metrics.total_turns if state else None,
                "model_response": normalized,
            },
        )
        return normalized

    def _parse_decision(
        self, response: Mapping[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        if response.get("search_action") == "STOP":
            if "search_actions" in response:
                raise BaselineProtocolError("STOP cannot include search_actions")
            reason = response.get("action_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise BaselineProtocolError("STOP.action_reason is required")
            return "STOP", []

        actions = response.get("search_actions")
        if not isinstance(actions, list) or not actions:
            raise BaselineProtocolError(
                "teacher must return non-empty search_actions or search_action=STOP"
            )
        if len(actions) > self.config.max_search_actions_per_turn:
            raise BaselineProtocolError(
                "search_actions exceeds max_search_actions_per_turn="
                f"{self.config.max_search_actions_per_turn}"
            )
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(actions):
            if not isinstance(raw, Mapping):
                raise BaselineProtocolError(f"search_actions[{index}] must be an object")
            action = dict(raw)
            for field_name in ("action_reason", "search_goal"):
                value = action.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise BaselineProtocolError(
                        f"search_actions[{index}].{field_name} is required"
                    )
            queries = action.get("search_queries")
            if not isinstance(queries, list) or not queries or not all(
                isinstance(query, str) and query.strip() for query in queries
            ):
                raise BaselineProtocolError(
                    f"search_actions[{index}].search_queries must be non-empty strings"
                )
            if len(queries) > self.config.max_queries_per_action:
                raise BaselineProtocolError(
                    f"search_actions[{index}] exceeds max_queries_per_action="
                    f"{self.config.max_queries_per_action}"
                )
            normalized.append(action)
        return "SEARCH", normalized

    def _execute_search_action(
        self,
        action: dict[str, Any],
        state: BaselineState,
        turn_index: int,
    ) -> dict[str, Any]:
        self._stage = "web_search"
        queries = [query.strip() for query in action["search_queries"]]
        state.metrics.search_action_count += 1
        state.metrics.search_query_count += len(queries)
        search_calls: list[dict[str, Any]] = []
        search_errors: list[dict[str, str]] = []
        action_webs: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for query in queries:
            started_at = time.perf_counter()
            self.logger.info(
                "web_search_started: query=%s",
                query,
                extra={
                    "event": "web_search_started",
                    "run_id": state.run_id,
                    "scheme": state.metrics.scheme,
                    "turn_id": turn_index,
                    "search_goal": action["search_goal"],
                    "query": query,
                    "top_k": self.config.top_k,
                },
            )
            try:
                results = self.web_search(query, top_k=self.config.top_k)
                if not isinstance(results, list):
                    raise TypeError("web_search must return a list")
                limited = results[: self.config.top_k]
                state.metrics.web_result_count += len(limited)
                registered = self._register_webs(limited, state)
                web_ids = [web["web_id"] for web in registered]
                search_calls.append({"query": query, "web_ids": web_ids})
                for web in registered:
                    if web["web_id"] not in seen_ids:
                        action_webs.append(web)
                        seen_ids.add(web["web_id"])
                self.logger.info(
                    "web_search_completed: results=%s duration_ms=%s",
                    len(registered),
                    round((time.perf_counter() - started_at) * 1000, 2),
                    extra={
                        "event": "web_search_completed",
                        "run_id": state.run_id,
                        "scheme": state.metrics.scheme,
                        "turn_id": turn_index,
                        "query": query,
                        "result_count": len(registered),
                        "web_ids": web_ids,
                    },
                )
            except Exception as exc:
                search_errors.append(
                    {"query": query, "error": f"{type(exc).__name__}: {exc}"}
                )
                self.logger.warning(
                    "web_search_failed: query=%s error=%s",
                    query,
                    exc,
                    exc_info=True,
                    extra={
                        "event": "web_search_failed",
                        "run_id": state.run_id,
                        "scheme": state.metrics.scheme,
                        "turn_id": turn_index,
                        "query": query,
                        "error_type": type(exc).__name__,
                    },
                )

        return {
            "action_reason": action["action_reason"],
            "search_goal": action["search_goal"],
            "search_queries": queries,
            "search_calls": search_calls,
            "search_errors": search_errors,
            "webs": copy.deepcopy(action_webs),
        }

    def _register_webs(
        self, results: list[dict[str, Any]], state: BaselineState
    ) -> list[dict[str, Any]]:
        registered: list[dict[str, Any]] = []
        for result in results:
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

    def _finish_metrics(
        self, state: BaselineState, termination_type: str, final_action: str
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

    def _checkpoint(self, state: BaselineState) -> None:
        state.metrics.unique_web_count = len(state.webs)
        if not self.output_path:
            return
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
                "scheme": state.metrics.scheme,
                "run_status": state.run_status,
                "output_path": str(self.output_path.resolve()),
                "metrics": state.metrics.to_dict(),
            },
        )
