"""Command-line entry point for a live trajectory synthesis run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import config.config as cfg

try:  # Support both ``python -m deep_search_claude`` and ``python deep_search_claude/cli.py``.
    from .observability import configure_logging
    from .orchestrator import DeepSearchOrchestrator, OrchestratorConfig
    from .paths import build_trajectory_path
except ImportError:  # pragma: no cover - used only by direct script execution.
    from observability import configure_logging
    from orchestrator import DeepSearchOrchestrator, OrchestratorConfig
    from paths import build_trajectory_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行双 Agent deep-search 轨迹合成")
    default_query = "小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？"
    default_query = "目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？"
    default_query = "梳理主要经济体针对人工智能生成内容的监管政策差异。"
    default_date = "2026年08月08日"
    parser.add_argument("--query", default=default_query, help="用户原始 query")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="轨迹 JSON 输出路径；默认写入 deep_search_claude/trajectories/",
    )
    parser.add_argument(
        "--current-date",
        default=default_date,
        help="注入模型的当前日期",
    )
    parser.add_argument("--max-turns", type=int, default=cfg.search_max_turns)
    parser.add_argument("--top-k", type=int, default=cfg.search_candidates_top_k)
    parser.add_argument("--max-queries-per-action", type=int, default=cfg.max_queries_per_action)
    parser.add_argument("--max-web-content-chars", type=int, default=cfg.max_web_content_chars)
    parser.add_argument("--model-max-attempts", type=int, default=cfg.model_max_attempts)
    parser.add_argument(
        "--model-retry-delay-seconds",
        type=float,
        default=cfg.model_retry_delay_seconds,
    )

    parser.add_argument(
        "--log-file",
        type=Path,
        help="JSON Lines 日志文件；默认与轨迹文件同名、扩展名为 .log",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="日志级别；DEBUG 会记录完整模型 JSON 响应",
    )
    args = parser.parse_args()
    if args.output is None:
        args.output = build_trajectory_path(
            "./deep_search_claude/trajectories",
            args.query,
            args.current_date,
        )
    if args.log_file is None:
        args.log_file = args.output.with_suffix(".log")
    return args


def main() -> int:
    args = parse_args()
    logger = configure_logging(args.log_file, level=args.log_level)
    # Lazy imports keep unit tests independent from optional API SDKs and keys.
    from get_model_infer import request_model
    from tools.web_search import web_search

    orchestrator = DeepSearchOrchestrator(
        request_model,
        web_search,
        config=OrchestratorConfig(
            max_turns=args.max_turns,
            top_k=args.top_k,
            max_queries_per_action=args.max_queries_per_action,
            max_web_content_chars=args.max_web_content_chars,
            model_max_attempts=args.model_max_attempts,
            model_retry_delay_seconds=args.model_retry_delay_seconds,
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
                    "hint": "使用 --log-level DEBUG 重跑可记录完整模型 JSON 响应",
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
                "goal_count": len(state.goals),
                "evidence_count": len(state.evidences),
                "conflict_count": len(state.conflicts),
                "web_count": len(state.webs),
                "turn_count": len(state.turns),
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
