"""Run with: python -m traj_web_extractor INPUT_JSON [-o OUTPUT_JSON]."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .extractor import extract_search_traj_file


def main() -> int:
    parser = argparse.ArgumentParser(description="按 goals.web_ids 匹配并抽取搜索目标及完整网页")
    parser.add_argument("input", type=Path, help="原始 trajectory JSON 文件")
    parser.add_argument(
        "-o", "--output", type=Path,
        help="输出路径；默认保存在 traj_web_extractor/output/，不覆盖已有文件",
    )
    args = parser.parse_args()
    output_path = args.output
    if output_path is None:
        output_path = (
            Path(__file__).resolve().parent / "output"
            / f"{args.input.stem}_search_goals.json"
        )

    try:
        search_traj = extract_search_traj_file(args.input, output_path)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"提取失败：{exc}\n")

    print(json.dumps({
        "output_path": str(output_path.resolve()),
        "goal_count": len(search_traj["search_goals"]),
        "web_counts": [len(item["webs"]) for item in search_traj["search_goals"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
