"""Run Qwen3-8B passage extraction for each (search goal, webpage) pair."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .extractor import _array, _identifier, _object
from .quote_prompts import NO_RELEVANT_INFORMATION, build_quote_messages

ModelRequest = Callable[[list[dict[str, str]]], str]
RecordCallback = Callable[[dict[str, Any]], None]
LOGGER = logging.getLogger(__name__)


class QuoteFormatError(ValueError):
    """The model did not return valid passage strings."""


class QuoteExtractionError(RuntimeError):
    """A goal-web pair failed; keep one context and the last response for diagnosis."""

    def __init__(self, message: str, *, messages: list[dict[str, str]], raw_response: Any):
        super().__init__(message)
        self.messages = deepcopy(messages)
        self.raw_response = raw_response


def parse_quotes(response: str, web_content: str) -> list[str]:
    """Decode quoted strings separated by ｜ (also tolerate ASCII |).

    Separators inside strings, escaped quotes, backslashes and line breaks are
    preserved. Only the explicit no-information marker yields an empty list;
    empty responses and malformed output are errors. The local verbatim check
    below is left in its existing, disabled state.
    """
    if not isinstance(response, str):
        raise QuoteFormatError("model response must be a string")
    if not isinstance(web_content, str):
        raise ValueError("web_content must be a string")
    text = response.strip()
    if text == NO_RELEVANT_INFORMATION:
        return []
    if not text:
        raise QuoteFormatError("empty model response is not a no-information result")

    # Some models emit literal line breaks inside strings; preserve them too.
    decoder = json.JSONDecoder(strict=False)
    position = 0
    quotes = []
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text) or text[position] != '"':
            raise QuoteFormatError(f"expected a double-quoted passage at offset {position}")
        try:
            quote, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError as exc:
            raise QuoteFormatError(f"invalid quoted passage at offset {position}") from exc
        if not quote.strip():
            raise QuoteFormatError("empty passage; use 无相关信息 when no passage is relevant")
        # if quote not in web_content:
        #     raise QuoteFormatError(f"passage {len(quotes) + 1} is not a verbatim substring of web_content")
        quotes.append(quote)
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            return quotes
        if text[position] not in "｜|":
            raise QuoteFormatError(f"expected ｜ between passages at offset {position}")
        position += 1


def _request_qwen(messages: list[dict[str, str]]) -> str:
    # Lazy import: offline tests and the original extractor need no API SDK/key.
    from .req_qwen import req_qwen_model

    return req_qwen_model(messages)


def _prepare_jobs(data: Mapping[str, Any]) -> list[tuple[Any, str, Mapping[str, Any]]]:
    document = _object(data, "search_traj")
    if not isinstance(document.get("user_query"), str):
        raise ValueError("search_traj.user_query must be a string")
    goals = _array(document.get("search_goals"), "search_traj.search_goals")
    jobs = []
    # Validate the entire input before any potentially billable model request.
    for goal_index, raw_goal in enumerate(goals):
        goal_path = f"search_goals[{goal_index}]"
        goal = _object(raw_goal, goal_path)
        goal_id = goal.get("search_goal_id")
        _identifier(goal_id, f"{goal_path}.search_goal_id")
        if not isinstance(goal.get("search_goal"), str):
            raise ValueError(f"{goal_path}.search_goal must be a string")
        for web_index, raw_web in enumerate(_array(goal.get("webs"), f"{goal_path}.webs")):
            web_path = f"{goal_path}.webs[{web_index}]"
            web = _object(raw_web, web_path)
            if not isinstance(web.get("web_content"), str):
                raise ValueError(f"{web_path}.web_content must be a string")
            jobs.append((goal_id, goal["search_goal"], web))
    return jobs


def extract_goal_web_quotes(
    data: Mapping[str, Any],
    *,
    request_model: ModelRequest | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    on_record: RecordCallback | None = None,
) -> list[dict[str, Any]]:
    """Extract each goal-web pair independently, in goal/web order.

    The default request function uses req_qwen.py (Qwen/Qwen3-8B). Inject a
    request_model(messages) -> str callable for testing. Retries reuse the same
    messages; errors are never converted to empty quotes or silently skipped.
    on_record receives each successful record before requesting the next webpage;
    callback failures propagate without retrying the model or duplicating a save.
    """
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if (isinstance(retry_delay_seconds, bool)
            or not isinstance(retry_delay_seconds, (int, float))
            or not math.isfinite(retry_delay_seconds) or retry_delay_seconds < 0):
        raise ValueError("retry_delay_seconds must be a finite non-negative number")
    if request_model is not None and not callable(request_model):
        raise ValueError("request_model must be callable")
    if on_record is not None and not callable(on_record):
        raise ValueError("on_record must be callable")
    jobs = _prepare_jobs(data)
    requester = _request_qwen if request_model is None else request_model
    results = []
    for job_index, (goal_id, search_goal, web) in enumerate(jobs):
        web_id = web.get("web_id", web.get("id", "unknown"))
        messages = build_quote_messages(data["user_query"], search_goal, web["web_content"])
        LOGGER.info("Extracting %s/%s: goal=%s web=%s", job_index + 1, len(jobs), goal_id, web_id)
        for attempt in range(1, max_attempts + 1):
            raw_response = None
            try:
                raw_response = requester(deepcopy(messages))
                quotes = parse_quotes(raw_response, web["web_content"])
            except Exception as exc:
                LOGGER.warning("Extraction failed: goal=%s web=%s attempt=%s/%s error=%s",
                               goal_id, web_id, attempt, max_attempts, type(exc).__name__)
                if attempt == max_attempts:
                    raise QuoteExtractionError(
                        f"goal={goal_id}, web={web_id}, item={job_index + 1} failed after "
                        f"{max_attempts} attempts ({type(exc).__name__}: {exc})",
                        messages=messages, raw_response=raw_response,
                    ) from exc
                if retry_delay_seconds:
                    time.sleep(retry_delay_seconds)
            else:
                record = {
                    "search_goal_id": goal_id,
                    "search_goal": search_goal,
                    "web": deepcopy(dict(web)),
                    "quotes": quotes,
                }
                if on_record is not None:
                    on_record(deepcopy(record))
                results.append(record)
                break
    return results


def extract_goal_web_quotes_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    request_model: ModelRequest | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    on_record_saved: RecordCallback | None = None,
) -> list[dict[str, Any]]:
    """Read search_goals JSON and append each completed web record to JSONL.

    Every record is flushed/fsynced before the next model request. Model failures
    leave completed lines intact. A new file is reserved on the first successful
    record; pre-existing files remain protected (no automatic resume). Zero jobs
    produce an empty JSONL file; failure before the first record creates no file.
    The Python return value remains a list. No output_path means return-only mode.
    on_record_saved is called only after a record has been durably written.
    """
    source = Path(input_path)
    destination = Path(output_path) if output_path is not None else None
    if on_record_saved is not None and not callable(on_record_saved):
        raise ValueError("on_record_saved must be callable")
    if on_record_saved is not None and destination is None:
        raise ValueError("on_record_saved requires output_path")
    if destination is not None:
        if source.resolve() == destination.resolve():
            raise ValueError("output_path must not be the input search_goals file")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"output file already exists: {destination}")
    with source.open("r", encoding="utf-8-sig") as source_file:
        data = json.load(source_file)
    if destination is None:
        return extract_goal_web_quotes(
            data, request_model=request_model,
            max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds,
        )

    output_file = None

    def open_output():
        destination.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL protects existing data; O_APPEND makes every write append-only.
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o666)
        try:
            return os.fdopen(fd, "a", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise

    def append_record(record: dict[str, Any]) -> None:
        nonlocal output_file
        # Serialize the complete record first. Embedded newlines stay escaped so
        # each webpage occupies exactly one physical line.
        line = json.dumps(record, ensure_ascii=False) + "\n"
        if output_file is None:
            output_file = open_output()
        output_file.write(line)
        output_file.flush()
        os.fsync(output_file.fileno())
        if on_record_saved is not None:
            on_record_saved(deepcopy(record))

    try:
        results = extract_goal_web_quotes(
            data, request_model=request_model,
            max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds,
            on_record=append_record,
        )
        if output_file is None:
            output_file = open_output()
            output_file.flush()
            os.fsync(output_file.fileno())
        return results
    finally:
        if output_file is not None:
            output_file.close()
