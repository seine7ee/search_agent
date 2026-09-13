"""Edit INPUT_FILES and run this file; output paths are always automatic."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from traj_web_extractor.pipeline import run_batch_pipeline
from traj_web_extractor.quote_config import DEFAULT_EXTRACTION_MODE, DEFAULT_MODEL_PROVIDER


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 只需在这里填写原始轨迹文件列表，不要填写 search_goals 中间结果。
# 支持绝对路径；相对路径以 PROJECT_ROOT 为基准，与启动脚本的目录无关。
INPUT_FILES = [
    # "batch_trajectories/multi_agent/traj_任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？_2026-09-06_1788679864899.json",
    # "/path/to/another_raw_trajectory.json",
    "batch_trajectories/multi_agent/traj_全球人形机器人量产进展如何，主要厂商的时间表是什么？_2026-08-24_1787541059497.json",
    "batch_trajectories/multi_agent/traj_分析近五年全球半导体产业链的关键并购及其影响。_2026-08-24_1787539207167.json",
    "batch_trajectories/multi_agent/traj_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。_2026-08-24_1787537840987.json",
    "batch_trajectories/multi_agent/traj_比较中国主要云计算厂商最新公开的收入、增速和市场定位。_2026-08-24_1787540246054.json",
    "batch_trajectories/multi_agent/traj_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？_2026-08-24_1787538403874.json",
    "batch_trajectories/multi_agent/traj_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？_2026-08-24_1787542217694.json",
    "batch_trajectories/multi_agent/traj_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？_2026-08-24_1787535881631.json",
    "batch_trajectories/multi_agent/traj_摩拜单车最近一个完整财年的营收是多少?_2026-09-12_1789220468021.json"
]

# 文件级线程数；每个文件内部按目标、网页顺序调用模型。
# 默认 1，避免 req_qwen.py 的流式输出交错；可按接口限流调整。
MAX_WORKERS = 2
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0
# "verbatim" 使用原文片段；"sentence_ids" 使用句子编号；
# "sentence_ranges" 使用语义片段的句子起止区间。
EXTRACTION_MODE = DEFAULT_EXTRACTION_MODE
# "qwen" 调用 req_qwen_model；"ds" 调用 req_ds.request_model。
MODEL_PROVIDER = DEFAULT_MODEL_PROVIDER

print(f"extraction_mode: {EXTRACTION_MODE}")
print(f"model_provider: {MODEL_PROVIDER}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
    try:
        if not isinstance(INPUT_FILES, (list, tuple)):
            raise ValueError("INPUT_FILES 必须是原始 JSON 文件的列表")
        inputs = []
        for item in INPUT_FILES:
            path = Path(item).expanduser()
            inputs.append(path if path.is_absolute() else PROJECT_ROOT / path)
        report = run_batch_pipeline(
            inputs, max_workers=MAX_WORKERS,
            max_attempts=MAX_ATTEMPTS, retry_delay_seconds=RETRY_DELAY_SECONDS,
            extraction_mode=EXTRACTION_MODE,
            model_provider=MODEL_PROVIDER,
        )
    except (TypeError, ValueError) as exc:
        print(f"批跑配置错误：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
