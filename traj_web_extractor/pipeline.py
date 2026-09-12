"""Orchestrate raw trajectory -> search goals -> per-web quotes."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from .extractor import extract_search_traj_file
from .quote_config import resolve_extraction_mode, resolve_model_provider
from .quote_extractor import ModelRequest, extract_goal_web_quotes_file

from traj_web_extractor.quote_config import DEFAULT_EXTRACTION_MODE, DEFAULT_MODEL_PROVIDER

MODULE_DIR = Path(__file__).resolve().parent
SEARCH_GOALS_DIR = MODULE_DIR / "search_goals"
WEBS_QUOTES_DIR = MODULE_DIR / "webs_quotes"
LOGGER = logging.getLogger(__name__)


def _validate_options(
    request_model: ModelRequest | None,
    max_attempts: int,
    retry_delay_seconds: float,
    extraction_mode: str | None,
    model_provider: str | None,
) -> tuple[str, str]:
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if (isinstance(retry_delay_seconds, bool)
            or not isinstance(retry_delay_seconds, (int, float))
            or not math.isfinite(retry_delay_seconds) or retry_delay_seconds < 0):
        raise ValueError("retry_delay_seconds must be a finite non-negative number")
    if request_model is not None and not callable(request_model):
        raise ValueError("request_model must be callable")
    return resolve_extraction_mode(extraction_mode), resolve_model_provider(model_provider)


def _output_paths(source: Path) -> tuple[Path, Path]:
    # Keep Chinese names readable without exceeding common 255-byte name limits.
    source_stem = source.stem.encode("utf-8")[:180].decode("utf-8", errors="ignore") or "trajectory"
    run_tag = f"{time.time_ns() // 1_000_000}_{uuid4().hex[:12]}"
    name = f"{DEFAULT_EXTRACTION_MODE}_{DEFAULT_MODEL_PROVIDER}_{source_stem}__{run_tag}"
    return (
        SEARCH_GOALS_DIR / f"{name}_search_goals.json",
        WEBS_QUOTES_DIR / f"{name}_quotes.jsonl",
    )


def run_trajectory_pipeline(
    input_path: str | Path,
    *,
    request_model: ModelRequest | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    extraction_mode: str | None = None,
    model_provider: str | None = None,
) -> dict[str, Any]:
    """Process one raw JSON with fully automatic, non-overwriting output paths.

    Return a SUCCESS/FAILED report. A quote-stage failure retains the intermediate
    file and any completed JSONL records, with their path and saved counts.
    Invalid function options raise ValueError before work starts. Each invocation
    is a fresh run, not a cache hit or a quote-level resume.
    """
    mode, provider = _validate_options(
        request_model, max_attempts, retry_delay_seconds, extraction_mode, model_provider,
    )
    source = Path(input_path)
    started = time.monotonic()
    report: dict[str, Any] = {
        "input_path": str(source),
        "extraction_mode": mode,
        "model_provider": provider,
        "status": "FAILED",
        "search_goals_path": None,
        "quotes_path": None,
        "quotes_complete": False,
        "goal_count": None,
        "web_count": None,
        "record_count": None,
        "quote_count": None,
        "failed_stage": None,
        "error": None,
    }
    stage = "search_goals"
    try:
        source = source.expanduser().resolve()
        report["input_path"] = str(source)
        goals_path, quotes_path = _output_paths(source)
        LOGGER.info("Starting search_goals extraction: %s", source)
        search_traj = extract_search_traj_file(source, goals_path)
        report["search_goals_path"] = str(goals_path)
        report["goal_count"] = len(search_traj["search_goals"])
        report["web_count"] = sum(len(goal["webs"]) for goal in search_traj["search_goals"])

        stage = "quotes"
        LOGGER.info("Saved %s; starting quote extraction (%s goal-web pairs)",
                    goals_path, report["web_count"])

        def record_saved(record: dict[str, Any]) -> None:
            report["quotes_path"] = str(quotes_path)
            report["record_count"] = (report["record_count"] or 0) + 1
            report["quote_count"] = (report["quote_count"] or 0) + len(record["quotes"])

        records = extract_goal_web_quotes_file(
            goals_path, quotes_path, request_model=request_model,
            max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds,
            on_record_saved=record_saved,
            extraction_mode=mode,
            model_provider=provider,
        )
        report["quotes_path"] = str(quotes_path)
        report["record_count"] = len(records)
        report["quote_count"] = sum(len(record["quotes"]) for record in records)
        report["quotes_complete"] = True
        report["status"] = "SUCCESS"
        LOGGER.info("Completed trajectory: %s -> %s", source, quotes_path)
    except Exception as exc:
        report["failed_stage"] = stage
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        LOGGER.error("Trajectory failed: input=%s stage=%s error=%s: %s",
                     source, stage, type(exc).__name__, exc)
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    return report


def run_batch_pipeline(
    input_paths: Sequence[str | Path],
    *,
    max_workers: int = 1,
    request_model: ModelRequest | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    extraction_mode: str | None = None,
    model_provider: str | None = None,
) -> dict[str, Any]:
    """Process a file list; isolate file failures and return input-ordered reports.

    Concurrency is per original file, not per webpage. Default sequential runs
    keep the existing streaming client's console output readable. Injected model
    callables must be thread-safe when max_workers > 1.
    """
    if isinstance(input_paths, (str, bytes, Path)) or not isinstance(input_paths, Sequence):
        raise ValueError("input_paths must be a list or tuple of JSON paths")
    if any(not isinstance(path, (str, Path)) for path in input_paths):
        raise ValueError("each input path must be a string or Path")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    mode, provider = _validate_options(
        request_model, max_attempts, retry_delay_seconds, extraction_mode, model_provider,
    )
    inputs = list(input_paths)
    worker = partial(
        run_trajectory_pipeline, request_model=request_model,
        max_attempts=max_attempts, retry_delay_seconds=retry_delay_seconds,
        extraction_mode=mode,
        model_provider=provider,
    )
    started = time.monotonic()
    if max_workers == 1 or len(inputs) < 2:
        results = [worker(path) for path in inputs]
    else:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="quote-pipeline") as executor:
            results = list(executor.map(worker, inputs))
    success_count = sum(result["status"] == "SUCCESS" for result in results)
    return {
        "extraction_mode": mode,
        "model_provider": provider,
        "total_count": len(inputs),
        "success_count": success_count,
        "failed_count": len(inputs) - success_count,
        "duration_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }
