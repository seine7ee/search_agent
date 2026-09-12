"""CLI for scheme-B single-agent trajectory synthesis."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import config.config as cfg
from deep_search.observability import configure_logging
from deep_search.paths import build_trajectory_path

from .orchestrator import BaselineConfig, BaselineOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行单 Agent Deep Search baseline")
    parser.add_argument("--query", required=True, help="用户原始 query")
    parser.add_argument(
        "--current-date",
        default=date.today().isoformat(),
        help="注入教师模型的当前日期",
    )
    parser.add_argument("--max-turns", type=int, default=cfg.search_max_turns)
    parser.add_argument("--top-k", type=int, default=cfg.search_candidates_top_k)
    parser.add_argument("--max-search-actions-per-turn", type=int, default=3)
    parser.add_argument(
        "--max-queries-per-action", type=int, default=cfg.max_queries_per_action
    )
    parser.add_argument(
        "--max-web-content-chars", type=int, default=cfg.max_web_content_chars
    )
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    args = parser.parse_args()
    if args.output is None:
        args.output = build_trajectory_path(
            "deep_search_baseline/trajectories",
            args.query,
            args.current_date,
        )
    if args.log_file is None:
        args.log_file = args.output.with_suffix(".log")
    return args


def main() -> int:
    args = parse_args()
    logger = configure_logging(
        args.log_file,
        level=args.log_level,
        logger_name="deep_search_baseline",
    )
    from get_model_infer import request_model
    from tools.web_search import web_search

    orchestrator = BaselineOrchestrator(
        request_model,
        web_search,
        config=BaselineConfig(
            max_turns=args.max_turns,
            top_k=args.top_k,
            max_search_actions_per_turn=args.max_search_actions_per_turn,
            max_queries_per_action=args.max_queries_per_action,
            max_web_content_chars=args.max_web_content_chars,
        ),
        output_path=args.output,
        logger=logger.getChild("orchestrator"),
    )
    try:
        state = orchestrator.run(args.query, current_date=args.current_date)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "run_status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "trajectory": str(args.output.resolve()),
                    "log_file": str(args.log_file.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "run_status": state.run_status,
                "stop_reason": state.stop_reason,
                "metrics": state.metrics.to_dict(),
                "output": str(args.output.resolve()),
                "log_file": str(args.log_file.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
