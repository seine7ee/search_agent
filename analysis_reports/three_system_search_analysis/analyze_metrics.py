"""Aggregate comparable execution metrics for the three search systems.

This script is intentionally read-only with respect to trajectory inputs.  It
prints a JSON payload that can be redirected or consumed by the report builder.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path


ROOT = Path("/Users/seinelee/Documents/my_search_agent")

FILES = {
    "A": [
        ROOT / "batch_trajectories/multi_agent/traj_全球人形机器人量产进展如何，主要厂商的时间表是什么？_2026-08-24_1787541059497.json",
        ROOT / "batch_trajectories/multi_agent/traj_分析近五年全球半导体产业链的关键并购及其影响。_2026-08-24_1787539207167.json",
        ROOT / "batch_trajectories/multi_agent/traj_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。_2026-08-24_1787537840987.json",
        ROOT / "batch_trajectories/multi_agent/traj_比较中国主要云计算厂商最新公开的收入、增速和市场定位。_2026-08-24_1787540246054.json",
        ROOT / "batch_trajectories/multi_agent/traj_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？_2026-08-24_1787538403874.json",
        ROOT / "batch_trajectories/multi_agent/traj_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？_2026-08-24_1787542217694.json",
        ROOT / "batch_trajectories/multi_agent/traj_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？_2026-08-24_1787535881631.json",
    ],
    "B": [
        ROOT / "batch_trajectories/scheme_b/traj_全球人形机器人量产进展如何，主要厂商的时间表是什么？_2026-09-04_1788537318100.json",
        ROOT / "batch_trajectories/scheme_b/traj_分析近五年全球半导体产业链的关键并购及其影响。_2026-09-04_1788537091465.json",
        ROOT / "batch_trajectories/scheme_b/traj_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。_2026-09-04_1788537023916.json",
        ROOT / "batch_trajectories/scheme_b/traj_比较中国主要云计算厂商最新公开的收入、增速和市场定位。_2026-09-04_1788537163351.json",
        ROOT / "batch_trajectories/scheme_b/traj_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？_2026-09-04_1788537085900.json",
        ROOT / "batch_trajectories/scheme_b/traj_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？_2026-09-04_1788537583883.json",
        ROOT / "batch_trajectories/scheme_b/traj_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？_2026-09-04_1788536770950.json",
    ],
    "C": [
        ROOT / "batch_trajectories/single_agent/traj_全球人形机器人量产进展如何，主要厂商的时间表是什么？_2026-08-24_1787535035524.json",
        ROOT / "batch_trajectories/single_agent/traj_分析近五年全球半导体产业链的关键并购及其影响。_2026-08-24_1787534135625.json",
        ROOT / "batch_trajectories/single_agent/traj_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。_2026-08-24_1787533454787.json",
        ROOT / "batch_trajectories/single_agent/traj_比较中国主要云计算厂商最新公开的收入、增速和市场定位。_2026-08-24_1787534574034.json",
        ROOT / "batch_trajectories/single_agent/traj_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？_2026-08-24_1787533630227.json",
        ROOT / "batch_trajectories/single_agent/traj_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？_2026-08-24_1787535197172.json",
        ROOT / "deep_search_single_agent/deep_search_single_agent/trajectories/traj_小米第二款车发布之后的下一个大型车展上，其竞品都发布了什么车_2026年08月26日_1787755354303.json",
    ],
}


def normalize_query(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def executed_queries(trajectory: dict) -> list[str]:
    if "turns" in trajectory:
        return [
            query
            for turn in trajectory.get("turns", [])
            for action in turn.get("search_actions", [])
            for query in action.get("search_queries", [])
        ]
    # In the goal-state systems, round N records the queries whose results are
    # consumed in that round.  Starting from round 2 avoids double-counting the
    # initialization action stored in round 1's execution payload.
    return [
        query
        for round_record in trajectory.get("rounds", [])[1:]
        for query in round_record.get("search_queries", [])
    ]


def evidence_web_ids(evidence: dict) -> set[str]:
    return {
        str(item.get("web_id"))
        for item in evidence.get("quotes", [])
        if item.get("web_id") is not None
    }


def summarize_file(system: str, path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    trajectory = payload["trajectory"]
    metrics = trajectory.get("metrics", {})
    queries = executed_queries(trajectory)
    normalized_queries = [normalize_query(query) for query in queries]
    query_counts = Counter(normalized_queries)
    evidences = trajectory.get("evidences", [])
    evidence_sources = [evidence_web_ids(evidence) for evidence in evidences]
    referenced_web_ids = set().union(*evidence_sources) if evidence_sources else set()
    goals = trajectory.get("goals", {})
    if isinstance(goals, dict):
        goal_values = list(goals.values())
    else:
        goal_values = list(goals)
    statuses = Counter(str(goal.get("status", "UNKNOWN")) for goal in goal_values)
    web_result_count = int(metrics.get("web_result_count", 0))
    unique_web_count = int(metrics.get("unique_web_count", len(trajectory.get("webs", []))))
    total_iterations = int(metrics.get("total_rounds", metrics.get("total_turns", 0)))
    search_iterations = int(metrics.get("search_rounds", metrics.get("search_turns", 0)))
    return {
        "system": system,
        "topic": trajectory.get("user_query"),
        "current_date": trajectory.get("current_date"),
        "path": str(path),
        "iterations": total_iterations,
        "search_iterations": search_iterations,
        "model_calls": int(metrics.get("model_call_count", 0)),
        "planner_calls": int(metrics.get("planner_call_count", 0)),
        "extractor_calls": int(metrics.get("evidence_extractor_call_count", 0)),
        "search_actions": int(metrics.get("search_action_count", search_iterations)),
        "queries": int(metrics.get("search_query_count", len(queries))),
        "parsed_query_count": len(queries),
        "exact_repeated_queries": sum(count - 1 for count in query_counts.values()),
        "web_results": web_result_count,
        "unique_webs": unique_web_count,
        "web_overlap_count": max(web_result_count - unique_web_count, 0),
        "web_overlap_rate": (
            (web_result_count - unique_web_count) / web_result_count
            if web_result_count
            else 0.0
        ),
        "elapsed_seconds": float(metrics.get("elapsed_ms", 0.0)) / 1000.0,
        "termination_type": metrics.get("termination_type"),
        "terminated_by_model": bool(metrics.get("terminated_by_model")),
        "reached_max_iterations": bool(
            metrics.get("reached_max_rounds", metrics.get("reached_max_turns", False))
        ),
        "evidence_count": len(evidences),
        "multi_web_evidence_count": sum(len(ids) >= 2 for ids in evidence_sources),
        "referenced_unique_webs": len(referenced_web_ids),
        "conflict_count": len(trajectory.get("conflicts", [])),
        "conflict_search_count": int(metrics.get("conflict_search_count", 0)),
        "goal_count": len(goal_values),
        "goal_statuses": dict(statuses),
        "unresolved_goal_count": sum(
            statuses.get(status, 0) for status in ("OPEN", "UNRESOLVED")
        ),
        "stop_reason": trajectory.get("stop_reason"),
    }


def mean(rows: list[dict], key: str) -> float:
    return statistics.mean(float(row[key]) for row in rows)


def aggregate(system: str, rows: list[dict]) -> dict:
    return {
        "system": system,
        "runs": len(rows),
        "mean_iterations": mean(rows, "iterations"),
        "mean_search_iterations": mean(rows, "search_iterations"),
        "mean_model_calls": mean(rows, "model_calls"),
        "mean_search_actions": mean(rows, "search_actions"),
        "mean_queries": mean(rows, "queries"),
        "mean_web_results": mean(rows, "web_results"),
        "mean_unique_webs": mean(rows, "unique_webs"),
        "mean_web_overlap_rate": mean(rows, "web_overlap_rate"),
        "mean_elapsed_seconds": mean(rows, "elapsed_seconds"),
        "queries_per_search_iteration": (
            sum(row["queries"] for row in rows)
            / sum(row["search_iterations"] for row in rows)
        ),
        "unique_webs_per_query": (
            sum(row["unique_webs"] for row in rows)
            / sum(row["queries"] for row in rows)
        ),
        "elapsed_seconds_per_query": (
            sum(row["elapsed_seconds"] for row in rows)
            / sum(row["queries"] for row in rows)
        ),
        "evidences_per_query": (
            sum(row["evidence_count"] for row in rows)
            / sum(row["queries"] for row in rows)
        ),
        "referenced_web_share": (
            sum(row["referenced_unique_webs"] for row in rows)
            / sum(row["unique_webs"] for row in rows)
        ),
        "model_stop_runs": sum(row["terminated_by_model"] for row in rows),
        "max_iteration_runs": sum(row["reached_max_iterations"] for row in rows),
        "mean_evidence_count": mean(rows, "evidence_count"),
        "mean_multi_web_evidence_count": mean(rows, "multi_web_evidence_count"),
        "mean_referenced_unique_webs": mean(rows, "referenced_unique_webs"),
        "mean_conflict_count": mean(rows, "conflict_count"),
        "total_conflict_searches": sum(row["conflict_search_count"] for row in rows),
        "mean_goal_count": mean(rows, "goal_count"),
        "runs_with_unresolved_goals": sum(row["unresolved_goal_count"] > 0 for row in rows),
        "total_exact_repeated_queries": sum(row["exact_repeated_queries"] for row in rows),
    }


def main() -> None:
    missing = [str(path) for paths in FILES.values() for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing trajectory files: " + ", ".join(missing))
    rows = {
        system: [summarize_file(system, path) for path in paths]
        for system, paths in FILES.items()
    }
    output = {
        "aggregates": {
            system: aggregate(system, system_rows)
            for system, system_rows in rows.items()
        },
        "runs": rows,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
