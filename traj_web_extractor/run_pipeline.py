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
from traj_web_extractor.quote_config import (
    DEFAULT_EXTRACTION_MODE,
    DEFAULT_MODEL_PROVIDER,
    EXTRACTION_MODES,
    MODEL_PROVIDERS,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="原始轨迹 -> search_goals -> quotes，自动保存两阶段结果")
    parser.add_argument("input", type=Path, help="原始轨迹 JSON 文件，无需指定输出路径")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--extraction-mode", choices=EXTRACTION_MODES, default=DEFAULT_EXTRACTION_MODE,
        help="相关信息抽取方案：verbatim=原文片段，sentence_ids=句子编号",
    )
    parser.add_argument(
        "--model-provider", choices=MODEL_PROVIDERS, default=DEFAULT_MODEL_PROVIDER,
        help="抽取模型：qwen=req_qwen_model，ds=req_ds.request_model",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        report = run_trajectory_pipeline(
            args.input, max_attempts=args.max_attempts, retry_delay_seconds=args.retry_delay_seconds,
            extraction_mode=args.extraction_mode,
            model_provider=args.model_provider,
        )
    except ValueError as exc:
        parser.exit(1, f"参数错误：{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
