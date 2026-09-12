# Deep Search 第一阶段轨迹合成

这是一个可最小运行的双 Agent 离线轨迹合成工程：

1. `search_planner` 创建/更新持久化搜索目标并规划 query；
2. 编排器调用 `web_search()`，归一化并去重网页；
3. `evidence_processor` 只基于当前目标与本轮网页抽取证据、识别冲突；
4. `search_planner` 根据新证据更新目标状态，并决定继续、扩展、验证、细化或停止；
5. 每轮模型 messages、原始响应、搜索 query、网页全文、证据、冲突与状态均写入轨迹；
6. 同一输出中生成精简的 `answer_agent_handoff`。

仓库同时提供不显式维护 goal/evidence/conflict 的单 Agent 方案 B，详见
`deep_search_baseline/README.md`。

## 目录

- `deep_search/prompts.py`：两个教师 Agent 的角色、约束和 JSON 协议。
- `deep_search/context.py`：首轮 Planner、非首轮 Planner 和 Evidence Processor 的上下文组装。
- `deep_search/orchestrator.py`：动作校验、搜索调度、状态维护、证据编号和轨迹收集。
- `deep_search/models.py`：目标与全局搜索状态。
- `deep_search/storage.py`：轨迹原子落盘。
- `docs/architecture.md`：数据流、上下文边界、状态机约束与第二阶段摊平建议。
- `get_model_infer.py`：教师模型 API 适配器，返回 JSON 对象。
- `tools/web_search.py`：根据配置选择搜索后端的统一入口。
- `tools/web_search_baidu.py`：百度搜索调用及 `references` 归一化。
- `tools/web_search_bocha.py`：Bocha 搜索调用及 `data.webPages.value` 归一化。

## 运行

安装依赖并配置 API：

```bash
python3 -m pip install -r requirements.txt
export ds_api_key="..."
export baidu_search_key1="..."
```

执行一次真实轨迹合成：

```bash
python3 -m deep_search \
  --query "小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？" \
  --current-date 2026-08-09 \
  --max-turns 8 \
  --top-k 10 \
  --output trajectory.json
```

模型和服务地址可用 `DS_MODEL`、`DS_BASE_URL` 环境变量覆盖。

未显式传入 `--output` 时，方案 A 和方案 B 的默认轨迹名统一为
`traj_{query}_{current_date}_{毫秒级时间戳}.json`，日志文件使用相同文件名和 `.log`
扩展名。

### 并发合成 10 条轨迹

批量入口不解析命令行参数。直接编辑 `run_batch_trajectories.py` 顶部的 `QUERIES`
（固定 10 条）、`MAX_WORKERS`（并发线程数 x）、`SCHEME`、`CURRENT_DATE` 和输出目录，
然后运行：

```bash
python3 run_batch_trajectories.py
```

每条 query 独立写入轨迹和日志；单条任务失败不会取消其他任务。程序结束后会按输入
顺序输出 10 条运行摘要和成功/失败计数。

### 搜索后端配置

统一搜索入口为：

```python
from tools.web_search import web_search

pages = web_search("你的 query", top_k=10)
```

在 `config/config.py` 中配置默认后端：

```python
search_engine = "baidu"  # 或 "bocha"
```

也可以通过环境变量覆盖：

```bash
export WEB_SEARCH_ENGINE="bocha"
```

`baidu` 使用 `baidu_search_key1`，`bocha` 使用 `bocha_search_key1`。配置为其他值时
统一入口会立即抛出包含支持列表的 `ValueError`。

### Bocha 搜索工具

配置 `bocha_search_key1` 后可直接调用：

```bash
export bocha_search_key1="..."
python3 -m tools.web_search_bocha "天空为什么是蓝色的？" --top-k 10
```

也可以在 Python 中使用：

```python
from tools.web_search_bocha import extract_web_pages, web_search

pages = extract_web_pages(response_payload)
pages = web_search("天空为什么是蓝色的？", top_k=10)
```

`extract_web_pages()` 只读取 `data.webPages.value`，将 `WebPages.0` 转成整数
ID `1`，并剔除 `isNavigational is True` 的结果。接口返回的 `null` 会作为非导航
网页保留，并在输出中规范为 `false`。

也支持直接运行脚本：

```bash
python3 deep_search/cli.py --query "你的问题"
```

## 日志监控与故障定位

每次运行默认在轨迹文件旁生成同名 `.log` 文件。控制台输出简明进度，日志文件是
JSON Lines 格式，单文件最大 10 MB，保留 5 个轮转文件。

需要检查完整教师模型 JSON 时使用：

```bash
python3 -m deep_search \
  --query "你的问题" \
  --log-level DEBUG \
  --log-file trajectories/debug.log
```

实时观察：

```bash
tail -f trajectories/debug.log
```

常用过滤方式：

```bash
# 查看错误和协议违规
grep -E '"level": "ERROR"|"event": "protocol_violation"' trajectories/debug.log

# 查看某个目标的完整事件链
grep '"search_goal_id": "GOAL_1"' trajectories/debug.log
```

日志事件包含 `run_id`、`turn_id`、`search_goal_id`、`agent`、`stage`、耗时、搜索
结果数和 evidence/conflict ID。运行失败时仍会写出轨迹，状态为 `FAILED`，其
`errors[].last_model_call` 保存当前失败尝试及错误，或异常前最后一次模型 JSON 响应。
正常返回的请求 messages 保存在对应 turn 的 Planner、Evidence Processor 或 Teacher
节点；请求失败或后续协议校验失败时保存在 `errors[].last_model_call.messages`。同一
逻辑请求发生多次重试时，messages 只保存一次，`attempts` 仅记录各次尝试状态和错误。

方案 A 遇到空内容或非 JSON 对象响应时默认最多请求 3 次，退避间隔从 1 秒开始；
可在 `config/config.py` 中通过 `model_max_attempts` 和
`model_retry_delay_seconds` 调整。重试事件写入日志为 `model_call_retrying`。
状态协议中 `supporting_evidence_ids`、`conflicting_evidence_ids` 统一使用 `E*`
证据 ID，`VERIFY_CONFLICT.target_conflict_id` 使用 `C*` 冲突 ID。

## 轨迹结构

输出 JSON 顶层包含：

- `trajectory`：完整可蒸馏轨迹，包括每轮 Planner/Evidence Processor 的 messages 和 response；
- `answer_agent_handoff`：目标状态、证据、冲突和网页，用于后续 Answer Agent。

达到 `max_turns` 时保留已有信息并返回 `MAX_TURNS_REACHED`，不会伪造 STOP。

方案 A 的轨迹和 CLI 输出均包含 `metrics`：总轮次、搜索轮次、模型调用数、搜索
动作/query 数、网页数、耗时，以及 `MODEL_STOP / MAX_TURNS / FAILED` 终止类型。
其中 `terminated_by_model` 和 `reached_max_turns` 可直接用于离线聚合。方案 B 使用
完全相同的字段定义。

## 测试

测试使用假教师模型与假搜索函数，不需要网络和密钥：

```bash
python3 -m unittest discover -v
```
