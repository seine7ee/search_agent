"""Single-file entry point for the complete extraction pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traj_web_extractor.pipeline import run_trajectory_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="原始轨迹 -> search_goals -> quotes，自动保存两阶段结果")
    parser.add_argument("input", type=Path, help="原始轨迹 JSON 文件，无需指定输出路径")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        report = run_trajectory_pipeline(
            args.input, max_attempts=args.max_attempts, retry_delay_seconds=args.retry_delay_seconds,
        )
    except ValueError as exc:
        parser.exit(1, f"参数错误：{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
