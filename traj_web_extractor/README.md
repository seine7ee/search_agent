# 按搜索目标提取轨迹网页

原始轨迹的目标/网页提取只使用 Python 标准库，不调用模型或搜索引擎。新增的相关片段抽取复用 `req_qwen.py` 调用 Qwen3-8B。两种流程都不修改输入文件。

## 完整流程与批量运行（推荐入口）

输入原始轨迹 JSON，一次完成以下流程，无需指定任何输出路径：

1. 调用现有 `extract_search_traj_file`，抽取目标及其关联的完整网页。
2. 将中间结果存入 `traj_web_extractor/search_goals/`。
3. 读取已保存的中间结果，调用现有 Qwen3-8B 抽取器逐目标、逐网页抽取片段。
4. 每完成一个网页，将对应的四字段记录立即追加到 `traj_web_extractor/webs_quotes/` 下的 JSONL 文件，不再等待整条轨迹完成。

### 批量脚本

编辑 `run_batch_extraction.py` 顶部的原始文件列表：

```python
INPUT_FILES = [
    "/path/to/raw_trajectory_1.json",
    "/path/to/raw_trajectory_2.json",
]
MAX_WORKERS = 1
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0
```

列表支持绝对路径，也支持相对于项目根目录的路径；脚本内已填入现有“任贤齐和古天乐……”原始轨迹作为示例，不需要先生成 search_goals 文件。配置现有 `silicon_key` 环境变量并安装项目依赖后，在项目根目录运行：

```bash
python3 -m traj_web_extractor.run_batch_extraction
```

也可从其他工作目录直接运行脚本：

```bash
python3 /Users/seinelee/Documents/my_search_agent/traj_web_extractor/run_batch_extraction.py
```

`MAX_WORKERS` 控制原始文件级并发；每个文件内部仍按目标、网页顺序抽取。默认 1，避免原有流式输出交错，并降低接口限流风险。注入自定义模型函数并开启多线程测试时，调用方应保证该函数线程安全。

### 单文件入口与 Python 方法

```bash
python3 -m traj_web_extractor.run_pipeline /path/to/raw_trajectory.json
```

```python
from traj_web_extractor import run_trajectory_pipeline, run_batch_pipeline

report = run_trajectory_pipeline("/path/to/raw_trajectory.json")
batch_report = run_batch_pipeline(
    ["/path/to/raw_trajectory_1.json", "/path/to/raw_trajectory_2.json"],
    max_workers=2,
)
```

Python 方法也不需要指定输出路径。单文件返回执行报告；批量返回总数、成功数、失败数、耗时，以及按输入列表顺序排列的单文件报告。每条报告包含实际保存的 `search_goals_path`、`quotes_path`、目标数、网页配对数、记录数和片段数。`quotes_complete` 表示是否全部完成；失败时的 `record_count`、`quote_count` 是已确认写入的部分结果统计，不代表整条轨迹成功。

### 自动路径、重复运行与错误处理

目录以模块自身位置为基准，不随启动工作目录变化。两阶段使用同一个自动生成的标记，便于关联：

```text
traj_web_extractor/search_goals/原文件名__毫秒时间戳_随机标识_search_goals.json
traj_web_extractor/webs_quotes/原文件名__毫秒时间戳_随机标识_quotes.jsonl
```

文件名过长时只截短文件名前缀，完整输入路径保留在执行报告中。不同目录中的同名输入以及重复运行都会生成新文件，不覆盖已有结果。当前不做缓存、重复输入去重或断点续跑；同一输入重复列入或重新运行，会重新发起模型请求。

一个文件失败不会阻断整个批次：

- 原始 JSON 读取或目标抽取失败：`failed_stage` 为 `search_goals`，不调用模型。
- 模型抽取失败：`failed_stage` 为 `quotes`，保留中间结果和已追加的完整 JSONL 记录；`quotes_path` 指向部分结果，`quotes_complete` 为 false。首个网页就失败、尚未保存任何记录时，`quotes_path` 为 null。
- `status` 为 `FAILED` 时，`error` 提供错误类型与原因；有结果路径也不代表整个任务完成，不将错误伪装成空 quotes。
- 脚本最终输出 JSON 汇总。全部成功退出码为 0，有失败或配置无效时退出码为 1。空列表视为无任务，正常退出。

中间数据格式和每条 quotes 记录的四字段结构不变；quotes 文件容器改为逐行追加的 JSONL。原有的网页编号、自定义 prompt 和 `req_qwen.py` 不由编排层改写。以下原有分阶段入口继续可用；要使用自动双目录流程，请使用本节入口。

### 流程测试（不调用真实 API）

```bash
python3 -m unittest traj_web_extractor.test_pipeline traj_web_extractor.test_jsonl_append -v
```

## 分阶段：原始轨迹抽取的 Python 调用

```python
from traj_web_extractor import extract_search_traj_file

search_traj = extract_search_traj_file(
    input_path="/path/to/trajectory.json",
    output_path="/path/to/search_traj.json",  # 可省略：仅返回字典，不保存文件
)
```

已有加载后的字典时，调用 `extract_search_traj(data)`；既支持包含 `trajectory` 的完整文档，也支持其内部的 trajectory 对象。

## 命令行调用

在项目根目录 `/Users/seinelee/Documents/my_search_agent` 运行：

```bash
python3 -m traj_web_extractor "batch_trajectories/multi_agent/traj_任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？_2026-09-06_1788679864899.json"
```

默认保存到本模块的 `output/原文件名_search_goals.json`。可以使用 `-o` 指定其他输出文件；已有文件不会被覆盖，重复运行时请指定新的文件名。旧版按轮次生成的 `_search_traj.json` 文件保留，不代表新版输出。

## 输出约定

输出顶层仅包含 `user_query`、`search_goals`。`user_query` 来自 `trajectory.user_query`；按 `trajectory.goals` 的原顺序遍历每个目标，输出：

| 字段 | 提取规则 |
|---|---|
| search_goal_id | 原始 `goal.search_goal_id`，不重新编号 |
| search_goal | 原始 `goal.search_goal` |
| webs | 按 `goal.web_ids` 的顺序，从 `trajectory.webs` 匹配得到的完整网页对象，保留全部原始字段 |
| web_content_str | 复用当前 `extractor.py` 的拼装逻辑：逐项添加 `[1]`、`[2]` 等编号并换行拼接；空列表对应空字符串。模型抽取仍读取单页原始 `web_content`，不读取此拼接字段 |

新版以目标及其网页引用为准，不再读取 `rounds[*].execution.webs`，也不依赖轮次是否完整记录。所有目标均保留，不按目标状态过滤。

匹配时优先使用网页的 `web_id`，仅在其缺失或 null 时兼容 `id` 字段。整数 ID 和对应字符串 ID（例如 `1` 和 `"1"`）可以匹配，但不修改输出中的原始 ID。若同一对象同时存在不同的 `web_id` 和 `id`，以 `web_id` 为准，避免误用其他标识。

网页不去重、不截断、不清理空白，空字符串也保留。多个目标引用同一网页时，各自保留；同一目标重复引用一个 ID 时也按原顺序保留。输出网页对象是独立副本，不会与输入共享可变字段。

缺失或 null 的 `goal.web_ids` 按空列表处理。未匹配的 ID、全局网页 ID 冲突、已选网页的 `web_content` 缺失/null/非字符串，以及其他结构类型错误均抛出带字段路径的 `ValueError`，不静默丢弃引用或内容。

指定样例共有 2 个目标：G1 关联 30 个网页，G2 关联 9 个网页；全局网页池为 36 个网页，部分网页由两个目标共同引用。

## 测试

```bash
python3 -m unittest traj_web_extractor.test_extractor -v
```

## 逐网页相关信息抽取（Qwen3-8B）

输入为上一阶段生成的 `search_goals.json`（顶层包含 `user_query` 和 `search_goals`）。

### 直接运行

在 `run_quote_extraction.py` 中设置 `INPUT_PATH`、`OUTPUT_PATH`、`MAX_ATTEMPTS` 和 `RETRY_DELAY_SECONDS` 后运行：

```bash
python3 -m traj_web_extractor.run_quote_extraction
```

默认输入已指向本目录中的“任贤齐和古天乐……”样例；也支持直接运行该 Python 文件，或通过命令行指定文件：

```bash
python3 -m traj_web_extractor.run_quote_extraction /path/to/search_goals.json -o /path/to/quotes.jsonl
```

需要安装项目 `requirements.txt` 中的依赖，并按现有 `req_qwen.py` 配置 `silicon_key` 环境变量。默认复用其 `Qwen/Qwen3-8B` 模型、服务地址和流式请求逻辑，不修改该文件。执行入口会真实请求模型；测试使用模拟响应，不需要密钥。

### Python 调用

```python
from traj_web_extractor import extract_goal_web_quotes_file

records = extract_goal_web_quotes_file(
    input_path="/path/to/search_goals.json",
    output_path="/path/to/quotes.jsonl",  # 可省略：只返回结果列表
    max_attempts=3,
    retry_delay_seconds=1.0,
)
```

已有字典时调用 `extract_goal_web_quotes(data)`。测试时可注入 `request_model(messages) -> str`，不改变生产模型调用代码。

### 上下文与抽取规则

遍历每个目标的每个网页，每个 `(search_goal, web)` 独立发起一次模型调用。上下文只包含：

- `system`：相关性和原文抽取规则、输出格式。
- `user`：当前时间（带时区的时间及 Unix 毫秒时间戳）、原始 `user_query`、当前 `search_goal`、当前网页的 `web_content`。

不会把目标下的全部网页、`web_content_str`、其他目标或历史响应传入本次调用。同一网页属于不同目标时分别抽取，不跨目标复用结果。

相关内容包括直接回答、重要背景或间接线索，以及能够纠正或反驳问题前提的负向信息。每个片段必须是当前网页中的连续原文；保留主语、否定、时间和条件，不改写或拼接不连续文本。

模型有相关信息时输出：

```text
"片段1"｜"片段2"
```

每个片段使用 JSON 字符串转义规则，原文中的引号、换行和反斜杠经解析恢复。解析器识别双引号边界，不直接按竖线切分，避免切坏片段中的 `｜`。完全无相关信息时输出约定标记 `无相关信息`，最终解析为 `quotes: []`。

当前 `parse_quotes` 中的原文子串校验已被注释，本次保留这一现有设置，因此代码不保证返回片段逐字存在于网页中。空响应、格式错误和网络错误仍视为失败，不能伪装成 `quotes: []`；默认最多尝试 3 次，同一网页重试复用同一份上下文和时间戳。

### 输出结果

结果文件使用 JSONL，每个目标—网页对应一行完整 JSON 对象，严格保留目标顺序和网页顺序，没有外层数组或行间逗号。例如下面是两条独立记录：

```jsonl
{"search_goal_id":"G1","search_goal":"当前搜索重点","web":{"web_id":"1","web_content":"网页中的原始内容"},"quotes":["网页中的原始内容"]}
{"search_goal_id":"G1","search_goal":"当前搜索重点","web":{"web_id":"2","web_content":"另一网页内容"},"quotes":[]}
```

`web` 保留原网页对象的所有字段，而不只是示例中的两个字段；无相关信息的网页也保留记录。网页或片段内部的换行会转义为 `\n`，不会把一条记录拆成多行。没有网页的目标不会生成虚构记录；整个输入没有网页时生成空 JSONL 文件。样例有 30 + 9 个目标—网页配对，成功完成后应有 39 行，不是 36 行。

每页解析成功后先追加一行，执行 `flush()` 与 `os.fsync()`，再发出下一页请求；不重写此前的记录。失败重试期间不写错误记录，最终成功后只写一次。磁盘写入失败直接停止，不重新请求模型，以免重复追加。

任一网页重试耗尽后抛出 `QuoteExtractionError`，已写入的行会保留。Python 调用方仍可读取异常的 `messages`（仅一份）和 `raw_response`（最后一次响应）定位问题。写入一行的过程中发生强制终止或磁盘故障时，末尾可能有不完整行，需检查尾行；此前已完成的行不重写。

追加是指同一次运行内逐页追加。已有输出仍拒绝覆盖或混入新的运行；自动编排会为重跑生成新文件，当前不做断点续跑。Python 方法成功时仍返回记录列表。文件内容统一为 JSONL，即使手工指定其他后缀也不会变为 JSON 数组，建议使用 `.jsonl` 后缀。读取方式改为逐行解析：

```python
import json

with open("/path/to/quotes.jsonl", encoding="utf-8") as file:
    records = [json.loads(line) for line in file if line.strip()]
```

### 新增流程测试

```bash
python3 -m unittest traj_web_extractor.test_jsonl_append -v
```

旧的严格原文校验测试与当前已注释的校验逻辑存在不一致；本次没有恢复该校验。
