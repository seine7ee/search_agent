"""Run ten trajectory-synthesis queries concurrently without CLI arguments.

Edit the constants in the configuration block below, then execute:

    python3 run_batch_trajectories.py
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import config.config as cfg
from deep_search.observability import configure_logging
# from deep_search.orchestrator import DeepSearchOrchestrator, OrchestratorConfig
from deep_search_claude.orchestrator import DeepSearchOrchestrator, OrchestratorConfig
from deep_search.orchestrator import DeepSearchOrchestrator as DeepSearchOrchestratorFullWebs
from deep_search.orchestrator import OrchestratorConfig as DeepSearchOrchestratorFullWebsConfig

from deep_search.paths import build_trajectory_path
from deep_search_baseline.orchestrator import BaselineConfig, BaselineOrchestrator
from deep_search_single_agent.orchestrator import (
    OrchestratorConfig as SingleAgentConfig,
    SingleAgentOrchestrator,
)
from deep_search_multi_agent.orchestrator import (
    MultiAgentOrchestrator,
    OrchestratorConfig as MultiAgentConfig,
)
from get_model_infer import request_model
from tools.web_search import web_search


SCHEMES = {"scheme_a", "scheme_b", "single_agent", "multi_agent", "scheme_a_full_webs"}
Orchestrator = (
    DeepSearchOrchestrator | BaselineOrchestrator | SingleAgentOrchestrator | MultiAgentOrchestrator | DeepSearchOrchestratorFullWebs
)


# ======================== Batch configuration ========================
# This list intentionally contains exactly ten queries. Replace the text in place
# when preparing a new batch; no command-line argument parsing is used.
QUERIES = [
    "小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？",
    "对比近三年全球主要车企的纯电动车销量及市场份额变化。",
    "梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。",
    "目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？",
    "分析近五年全球半导体产业链的关键并购及其影响。",
    "比较中国主要云计算厂商最新公开的收入、增速和市场定位。",
    "全球人形机器人量产进展如何，主要厂商的时间表是什么？",
    "近三年动力电池技术有哪些重要突破，分别由哪些企业推动？",
    "比较当前主流 AI 编程助手的功能、模型和企业部署能力。",
]

QUERIES = [
    "比较中国主要云计算厂商最新公开的收入、增速和市场定位。",
    "全球人形机器人量产进展如何，主要厂商的时间表是什么？",
    "近三年动力电池技术有哪些重要突破，分别由哪些企业推动？",
    "比较当前主流 AI 编程助手的功能、模型和企业部署能力。",
]

# QUERIES = [
#     "DeepSeek 创始人本科就读的大学，现任校长是谁？",
#     "《流浪地球2》导演的上一部长片，豆瓣评分多少？",
#     "ChatGPT 首次发布当天，英伟达股价收盘价是多少？",
#     "中国首次火星着陆当天，NASA 在任局长是谁？",
#     "特斯拉与比亚迪 2024 年全年销量相差多少辆？",
#     "最近两届冬奥会中国金牌总数比日本多几枚？",
#     "网传每天必须喝八杯水有科学依据吗？",
#     "各机构对 2025 年中国 GDP 增速预测差异有多大？",
#     "学术界对 Mamba 能否替代 Transformer 有哪些主要争议？",
#     "预算两万元，OLED 和 Mini LED 电视怎么选？"
# ]

# QUERIES = ["任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？"]

# 并发线程数 x：直接修改这个整数。
MAX_WORKERS = 1

# "scheme_a" 使用 deep_search_claude 双 Agent 方案；"scheme_b" 使用 deep_search_baseline 单 Agent baseline；
# "single_agent" 使用 deep_search_single_agent；"multi_agent" 使用 deep_search_multi_agent。
# 依次顺序运行的 schema 列表：本次运行会先完整跑完 single_agent 的所有 query，
# 再开始跑 multi_agent，不会并行跨 schema 执行。
# SCHEMES_TO_RUN = ["single_agent", "multi_agent"]
SCHEMES_TO_RUN = ["scheme_a_full_webs"]
CURRENT_DATE = date.today().isoformat()
LOG_LEVEL = "INFO"

# Both schemes use the same project-level search limits unless overridden here.
MAX_TURNS = cfg.search_max_turns
TOP_K = cfg.search_candidates_top_k
MAX_QUERIES_PER_ACTION = cfg.max_queries_per_action
MAX_WEB_CONTENT_CHARS = cfg.max_web_content_chars
MAX_SEARCH_ACTIONS_PER_TURN = cfg.search_max_turns
MODEL_MAX_ATTEMPTS = cfg.model_max_attempts
MODEL_RETRY_DELAY_SECONDS = cfg.model_retry_delay_seconds
# ====================================================================


def _output_directory_for(scheme: str) -> Path:
    """Each scheme writes its trajectories into its own subdirectory."""
    return Path(f"batch_trajectories/{scheme}")


def _close_logger(logger: logging.Logger) -> None:
    """Flush and detach per-run handlers after a worker finishes."""
    for handler in list(logger.handlers):
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        finally:
            logger.removeHandler(handler)


def _build_orchestrator(
    *,
    scheme: str,
    output_path: Path,
    logger: logging.Logger,
) -> Orchestrator:
    if scheme == "scheme_a":
        return DeepSearchOrchestrator(
            request_model,
            web_search,
            config=OrchestratorConfig(
                max_turns=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )
    if scheme == "scheme_b":
        return BaselineOrchestrator(
            request_model,
            web_search,
            config=BaselineConfig(
                max_turns=MAX_TURNS,
                top_k=TOP_K,
                max_search_actions_per_turn=MAX_SEARCH_ACTIONS_PER_TURN,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )
    if scheme == "single_agent":
        return SingleAgentOrchestrator(
            request_model,
            web_search,
            config=SingleAgentConfig(
                max_rounds=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )
    if scheme == "multi_agent":
        return MultiAgentOrchestrator(
            request_model,
            web_search,
            config=MultiAgentConfig(
                max_rounds=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )

    if scheme == "scheme_a_full_webs":
        return DeepSearchOrchestratorFullWebs(
            request_model,
            web_search,
            config=DeepSearchOrchestratorFullWebsConfig(
                max_turns=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )


    raise ValueError(f"SCHEME must be one of {sorted(SCHEMES)}")


def _total_iterations(metrics: Any) -> int:
    """Normalize the turn/round counter across schemes' metrics field names.

    scheme_a/scheme_b count in "turns" (deep_search_claude, deep_search_baseline);
    single_agent/multi_agent count in "rounds" (deep_search_single_agent,
    deep_search_multi_agent). Both mean the same thing: how many top-level
    iterations the run went through.
    """
    data = metrics.to_dict()
    return data.get("total_turns", data.get("total_rounds", 0))


def _synthesize_one(
    index: int,
    query: str,
    *,
    scheme: str,
    current_date: str,
    output_directory: Path,
) -> dict[str, Any]:
    output_path = build_trajectory_path(output_directory, query, current_date)
    log_path = output_path.with_suffix(".log")
    logger_name = f"trajectory_batch.{scheme}.worker_{index}.{output_path.stem}"
    logger: logging.Logger | None = None

    try:
        logger = configure_logging(
            log_path,
            level=LOG_LEVEL,
            logger_name=logger_name,
        )
        orchestrator = _build_orchestrator(
            scheme=scheme,
            output_path=output_path,
            logger=logger,
        )
        state = orchestrator.run(query, current_date=current_date)
        return {
            "index": index,
            "query": query,
            "scheme": scheme,
            "run_status": state.run_status,
            "stop_reason": state.stop_reason,
            "termination_type": state.metrics.termination_type,
            "total_turns": _total_iterations(state.metrics),
            "metrics": state.metrics.to_dict(),
            "output": str(output_path.resolve()),
            "log_file": str(log_path.resolve()),
        }
    except Exception as exc:
        # One failed query must not cancel the other nine worker tasks.
        return {
            "index": index,
            "query": query,
            "scheme": scheme,
            "run_status": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "output": str(output_path.resolve()),
            "log_file": str(log_path.resolve()),
        }
    finally:
        if logger is not None:
            _close_logger(logger)


def synthesize_queries(
    queries: Sequence[str],
    *,
    max_workers: int,
    scheme: str,
    current_date: str,
    output_directory: Path,
) -> list[dict[str, Any]]:
    """Synthesize a query collection concurrently and preserve input order."""
    if not queries or any(not isinstance(query, str) for query in queries):
        raise ValueError("queries must contain only non-empty strings")
    normalized_queries = [query.strip() for query in queries]
    if any(not query for query in normalized_queries):
        raise ValueError("queries must contain only non-empty strings")
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    if scheme not in SCHEMES:
        raise ValueError(f"scheme must be one of {sorted(SCHEMES)}")
    if not current_date.strip():
        raise ValueError("current_date cannot be empty")

    results: list[dict[str, Any] | None] = [None] * len(normalized_queries)
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="trajectory-synthesis",
    ) as executor:
        futures: dict[Future[dict[str, Any]], int] = {
            executor.submit(
                _synthesize_one,
                index,
                query,
                scheme=scheme,
                current_date=current_date,
                output_directory=output_directory,
            ): index
            for index, query in enumerate(normalized_queries, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index - 1] = future.result()
            except Exception as exc:  # Covers failures before worker logging starts.
                results[index - 1] = {
                    "index": index,
                    "query": normalized_queries[index - 1],
                    "scheme": scheme,
                    "run_status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

    return [result for result in results if result is not None]


def run_batch_trajectories(scheme: str) -> list[dict[str, Any]]:
    """Run the in-file queries for a single scheme with the in-file concurrency setting."""
    return synthesize_queries(
        QUERIES,
        max_workers=MAX_WORKERS,
        scheme=scheme,
        current_date=CURRENT_DATE,
        output_directory=_output_directory_for(scheme),
    )


def run_all_schemes() -> dict[str, list[dict[str, Any]]]:
    """Run every scheme in SCHEMES_TO_RUN, one at a time, in list order.

    Each scheme's queries run concurrently among themselves (MAX_WORKERS),
    but the next scheme only starts after the current one has fully
    finished -- e.g. with the default SCHEMES_TO_RUN, single_agent's ten
    queries all complete before multi_agent starts.
    """
    all_results: dict[str, list[dict[str, Any]]] = {}
    for scheme in SCHEMES_TO_RUN:
        print(f"=== 开始运行 scheme={scheme} ===", flush=True)
        results = run_batch_trajectories(scheme)
        succeeded = sum(result["run_status"] != "FAILED" for result in results)
        print(
            f"=== scheme={scheme} 完成：{succeeded}/{len(results)} 成功 ===",
            flush=True,
        )
        all_results[scheme] = results
    return all_results


def main() -> int:
    all_results = run_all_schemes()
    summary = {}
    overall_success = True
    for scheme, results in all_results.items():
        succeeded = sum(result["run_status"] != "FAILED" for result in results)
        failed = len(results) - succeeded
        if failed:
            overall_success = False
        summary[scheme] = {
            "query_count": len(results),
            "max_workers": MAX_WORKERS,
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }
    print(
        json.dumps(
            {
                "schemes_run": SCHEMES_TO_RUN,
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
