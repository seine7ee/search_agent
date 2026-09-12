"""Run directly, or with python -m traj_web_extractor.run_quote_extraction."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traj_web_extractor.quote_extractor import QuoteExtractionError, extract_goal_web_quotes_file


# 可直接修改这两个变量后运行本文件；也可以用命令行指定输入、输出路径。
INPUT_PATH = Path(__file__).resolve().parent / "output" / (
    "traj_任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？_2026-09-06_1788679864899_search_goals.json"
)
OUTPUT_PATH = INPUT_PATH.with_name(f"{INPUT_PATH.stem}_quotes.jsonl")
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 Qwen3-8B 逐目标、逐网页抽取相关原文片段")
    parser.add_argument("input", nargs="?", type=Path, help="search_goals.json 文件，省略时使用 INPUT_PATH")
    parser.add_argument("-o", "--output", type=Path, help="逐网页追加 JSONL（建议 .jsonl 后缀），已有文件不覆盖")
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--retry-delay-seconds", type=float, default=RETRY_DELAY_SECONDS)
    args = parser.parse_args()
    input_path = INPUT_PATH if args.input is None else args.input
    output_path = args.output
    if output_path is None:
        output_path = OUTPUT_PATH if args.input is None else input_path.with_name(f"{input_path.stem}_quotes.jsonl")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        results = extract_goal_web_quotes_file(
            input_path, output_path,
            max_attempts=args.max_attempts, retry_delay_seconds=args.retry_delay_seconds,
        )
    except (OSError, ValueError, QuoteExtractionError) as exc:
        parser.exit(1, f"相关片段抽取失败：{exc}\n")
    print(json.dumps({
        "output_path": str(output_path.resolve()),
        "record_count": len(results),
        "records_with_quotes": sum(bool(item["quotes"]) for item in results),
        "quote_count": sum(len(item["quotes"]) for item in results),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
