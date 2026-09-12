"""Extract the requested trajectory fields without inferring missing content."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any


def _object(value: Any, field_path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_path} must be an object")
    return value


def _array(value: Any, field_path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_path} must be an array")
    return value


def _identifier(value: Any, field_path: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        raise ValueError(f"{field_path} must be a non-empty string or integer")
    return str(value)


def extract_search_traj(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return user_query and goals with complete, ID-matched webpage objects.

    Accept either a saved document containing ``trajectory`` or the trajectory
    object itself. Keep goals in their original order and webpages in each goal's
    web_ids order, including shared/repeated references. Match against web_id,
    falling back to id only when web_id is missing/null; normalize integer IDs to
    strings for lookup without modifying output objects. Never infer from rounds.

    Missing/null goal.web_ids yields an empty list. Unmatched or ambiguous IDs and
    malformed selected web_content raise ValueError instead of losing content.
    """
    document = _object(data, "document")
    trajectory = _object(document.get("trajectory", document), "trajectory")
    user_query = trajectory.get("user_query")
    if not isinstance(user_query, str):
        raise ValueError("trajectory.user_query must be a string")
    goals = _array(trajectory.get("goals"), "trajectory.goals")
    web_items = _array(trajectory.get("webs"), "trajectory.webs")
    web_index = {}
    for index, raw_web in enumerate(web_items):
        web_path = f"trajectory.webs[{index}]"
        web = _object(raw_web, web_path)
        id_field = "web_id" if web.get("web_id") is not None else "id"
        web_id = _identifier(web.get(id_field), f"{web_path}.{id_field}")
        if web_id in web_index:
            previous_path = web_index[web_id][1]
            raise ValueError(f"{web_path}.{id_field}: duplicate web ID {web_id!r} (also in {previous_path})")
        web_index[web_id] = (web, web_path)

    search_goals = []
    for goal_index, raw_goal in enumerate(goals):
        field_path = f"trajectory.goals[{goal_index}]"
        goal = _object(raw_goal, field_path)
        search_goal_id = goal.get("search_goal_id")
        _identifier(search_goal_id, f"{field_path}.search_goal_id")
        search_goal = goal.get("search_goal")
        if not isinstance(search_goal, str):
            raise ValueError(f"{field_path}.search_goal must be a string")
        raw_web_ids = goal.get("web_ids")
        web_ids = _array([] if raw_web_ids is None else raw_web_ids, f"{field_path}.web_ids")
        webs = []
        for reference_index, raw_id in enumerate(web_ids):
            reference_path = f"{field_path}.web_ids[{reference_index}]"
            web_id = _identifier(raw_id, reference_path)
            if web_id not in web_index:
                raise ValueError(f"{reference_path}: web ID {web_id!r} not found in trajectory.webs")
            web, web_path = web_index[web_id]
            if not isinstance(web.get("web_content"), str):
                raise ValueError(f"{web_path}.web_content must be a string")
            webs.append(deepcopy(dict(web)))

        web_content_str = ""
        for i, web in enumerate(webs):
            web_content = web.get("web_content")
            web_content_str += f"[{i+1}]{web_content}\n"

        search_goals.append(
            {
                "search_goal_id": search_goal_id,
                "search_goal": search_goal,
                "webs": webs,
                "web_content_str": web_content_str.strip(),
            }
        )

    return {"user_query": user_query, "search_goals": search_goals}


def extract_search_traj_file(
    input_path: str | Path, output_path: str | Path | None = None
) -> dict[str, Any]:
    """Read a trajectory JSON, optionally save the extraction, and return it.

    Output is UTF-8 JSON with readable Chinese text. Existing output files are
    never overwritten. Omit output_path to obtain only the in-memory result.
    """
    source = Path(input_path)
    destination = Path(output_path) if output_path is not None else None
    if destination is not None and source.resolve() == destination.resolve():
        raise ValueError("output_path must not be the input trajectory file")

    with source.open("r", encoding="utf-8-sig") as source_file:
        search_traj = extract_search_traj(json.load(source_file))

    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as output_file:
            json.dump(search_traj, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")

    return search_traj

if __name__ == '__main__':
    path = "/Users/seinelee/Documents/my_search_agent/batch_trajectories/multi_agent/traj_任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？_2026-09-06_1788679864899.json"
    output_path = "/Users/seinelee/Documents/my_search_agent/batch_trajectories/multi_agent/traj_任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？_2026-09-06_1788679864899.webs.json"
    ll = extract_search_traj_file(path)
    print(ll)
