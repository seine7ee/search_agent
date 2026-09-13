# 按搜索目标提取轨迹网页

原始轨迹的目标/网页提取只使用 Python 标准库，不调用模型或搜索引擎。相关片段抽取可配置使用 `req_qwen.py` 的 Qwen3-8B 或 `req_ds.py` 的 DeepSeek 接口。两种流程都不修改输入文件。

## 抽取结果可视化

本目录提供一个无第三方依赖的本地检视页面。服务启动时会自动扫描 `traj_web_extractor/webs_quotes/` 下的全部 JSONL 文件，页面左侧可直接选择并加载任一结果。选中后按 search goal 分组、按 web 展开，集中展示用户 query、当前 search goal、网页元数据、拆分后的句子及编号、模型选中的 sentence IDs 或 sentence ranges、规则补全句子和最终 quotes。页面还支持 search goal 切换、全文过滤、网页批量展开/收起和原始 `web_content` 查看。

在项目根目录运行：

```bash
python3 -m traj_web_extractor.run_visualizer \
  "/path/to/quotes.jsonl"
```

然后访问 `http://127.0.0.1:8765`。命令中的文件路径只决定页面首次选中的文件，页面仍会列出扫描目录中的其他 JSONL；不传时优先打开当前“小米车展”样例。可用 `--host`、`--port` 修改监听地址，也可用 `--quotes-dir` 扫描其他目录：

```bash
python3 -m traj_web_extractor.run_visualizer \
  --quotes-dir "/path/to/webs_quotes" \
  "initial_quotes.jsonl"
```

首次选中的文件必须位于扫描目录内，接口也只允许按启动时生成的文件列表加载结果，不能通过页面读取目录外文件。quotes JSONL 本身不保存 `user_query`，页面会根据同一次运行的文件标识自动查找相邻模块 `search_goals/` 下的配套 JSON 并读取 query，也可为首次文件显式指定：

```bash
python3 -m traj_web_extractor.run_visualizer \
  "/path/to/quotes.jsonl" \
  --search-goals "/path/to/search_goals.json"
```

页面用青绿色标记模型实际选中的编号；当 quotes 合并规则在两个相距一个句子的编号之间自动补齐上下文时，该中间句以琥珀色标记。所有动态文本都作为纯文本渲染，网页 URL 仅允许 `http`/`https` 协议。

可视化模块测试：

```bash
python3 -m unittest traj_web_extractor.test_visualizer -v
```

## 完整流程与批量运行（推荐入口）

输入原始轨迹 JSON，一次完成以下流程，无需指定任何输出路径：

1. 调用现有 `extract_search_traj_file`，抽取目标及其关联的完整网页。
2. 将中间结果存入 `traj_web_extractor/search_goals/`。
3. 读取已保存的中间结果，调用配置的模型逐目标、逐网页抽取片段。
4. 每完成一个网页，将对应记录立即追加到 `traj_web_extractor/webs_quotes/` 下的 JSONL 文件，不再等待整条轨迹完成。

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
EXTRACTION_MODE = "sentence_ids"  # 或 "verbatim"、"sentence_ranges"
MODEL_PROVIDER = "ds"             # 或 "qwen"
```

列表支持绝对路径，也支持相对于项目根目录的路径；脚本内已填入现有样例，不需要先生成 search_goals 文件。使用 Qwen 时配置其接口凭证，使用 DeepSeek 时配置 `config.config.ds_api_key`，安装项目依赖后在项目根目录运行：

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

report = run_trajectory_pipeline(
    "/path/to/raw_trajectory.json",
    extraction_mode="sentence_ids",
    model_provider="ds",
)
batch_report = run_batch_pipeline(
    ["/path/to/raw_trajectory_1.json", "/path/to/raw_trajectory_2.json"],
    max_workers=2,
    model_provider="qwen",
)
```

Python 方法也不需要指定输出路径。单文件返回执行报告；批量返回总数、成功数、失败数、耗时，以及按输入列表顺序排列的单文件报告。报告会记录实际使用的 `extraction_mode`、`model_provider`、结果路径、目标数、网页配对数、记录数和片段数。`quotes_complete` 表示是否全部完成；失败时的 `record_count`、`quote_count` 是已确认写入的部分结果统计，不代表整条轨迹成功。

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

中间数据格式不变；quotes 文件容器为逐行追加的 JSONL。`verbatim` 保留原有四字段结果，`sentence_ids` 增加分句与编号字段，`sentence_ranges` 增加分句与语义片段起止区间。以下分阶段入口继续可用；要使用自动双目录流程，请使用本节入口。

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

## 逐网页相关信息抽取（模型可配置）

输入为上一阶段生成的 `search_goals.json`（顶层包含 `user_query` 和 `search_goals`）。

### 直接运行

在 `run_quote_extraction.py` 中设置 `INPUT_PATH`、`OUTPUT_PATH`、`MAX_ATTEMPTS`、`RETRY_DELAY_SECONDS`、`EXTRACTION_MODE` 和 `MODEL_PROVIDER` 后运行：

```bash
python3 -m traj_web_extractor.run_quote_extraction
```

默认输入已指向本目录中的“任贤齐和古天乐……”样例；也支持直接运行该 Python 文件，或通过命令行指定文件：

```bash
python3 -m traj_web_extractor.run_quote_extraction \
  /path/to/search_goals.json \
  -o /path/to/quotes.jsonl \
  --extraction-mode sentence_ids \
  --model-provider ds
```

需要安装项目 `requirements.txt` 中的依赖。`qwen` 复用 `req_qwen_model`；`ds` 调用 `req_ds.request_model`，后者默认使用流式入口。当前默认提供方以 `quote_config.py` 为准。执行入口会真实请求模型；测试使用模拟响应，不需要密钥。

### Python 调用

```python
from traj_web_extractor import extract_goal_web_quotes_file

records = extract_goal_web_quotes_file(
    input_path="/path/to/search_goals.json",
    output_path="/path/to/quotes.jsonl",  # 可省略：只返回结果列表
    max_attempts=3,
    retry_delay_seconds=1.0,
    extraction_mode="sentence_ids",       # 或 "verbatim"、"sentence_ranges"
    model_provider="ds",                  # 或 "qwen"
)
```

已有字典时调用 `extract_goal_web_quotes(data)`。测试时可注入 `request_model(messages) -> str`，不改变生产模型调用代码。

### 抽取方案与模型配置

三种上下文组装与输出解析方案相互独立，可通过同一个配置项切换：

| 配置值 | 上下文 | 模型输出 | 结果字段 |
|---|---|---|---|
| `verbatim` | 原始 `web_content` | `"片段1"｜"片段2"` 或 `无相关信息` | 原有四字段，包含 `quotes` |
| `sentence_ids` | 规则分句并显示为 `[1] 句子` | 严格 JSON 整数数组，如 `[3, 5]`；无相关信息为 `[]` | 增加完整 `sentences`、模型选择的 `sentence_ids`，并由程序重建 `quotes` |
| `sentence_ranges` | 与 `sentence_ids` 相同的规则分句与编号 | 起止区间，如 `[3, 5]\|[8, 8]`；无相关信息为 `[]` | 增加完整 `sentences`、模型选择的 `sentence_ranges`，并按闭区间重建 `quotes` |

抽取方案和模型提供方的默认值均位于 `quote_config.py`：

```python
DEFAULT_EXTRACTION_MODE = SENTENCE_RANGES_MODE
DEFAULT_MODEL_PROVIDER = DS_MODEL_PROVIDER
```

可通过以下任一种方式自由切换：

- 修改上述全局默认值。
- 批跑时修改 `run_batch_extraction.py` 中的 `EXTRACTION_MODE` 和 `MODEL_PROVIDER`。
- Python 调用传入 `extraction_mode="sentence_ids"`、`extraction_mode="sentence_ranges"`、`model_provider="ds"` 等参数。
- 单文件完整流程或分阶段入口传入 `--extraction-mode sentence_ranges --model-provider ds`。命令行参数会覆盖默认配置。

未显式传参时采用 `quote_config.py` 中的当前默认值；目前为 `sentence_ranges + ds`。原有 `quote_prompts.py` 的上下文组装和 `parse_quotes` 的解析行为不变。非法抽取模式或模型提供方会在任何模型请求前报错。显式注入 `request_model(messages) -> str` 时，注入函数优先于配置提供方，便于测试或接入第三种模型。

`req_ds.request_model_stream` 与 `request_model_standard` 均直接返回最终回答文本，不再将模型输出预解析为 JSON 对象；流式接口也不再强制 `json_object` 响应格式。因此它们可以原样返回编号数组 `[3, 5]`、区间串 `[3, 5]|[8, 8]`、逐字片段或 `无相关信息`，再由当前抽取方案自己的解析器校验。`request_model(messages, stream=True)` 默认使用流式入口，传入 `stream=False` 使用标准入口。

### 句子编号方案

`sentence_ids` 模式先清理网页中的换行，再进行分句。换行符本身永远不作为句子边界：英文单词或名字之间的换行替换为单个空格，例如 `Figure\nAI` 变成 `Figure AI`；中文字符之间的换行直接移除，例如 `波士顿动\n力` 变成 `波士顿动力`；标点前的换行也直接移除。连续换行统一按同一规则清理。

完成清理后，只按照明确的全角/半角句末符号 `。．｡.!！?？;；` 切分。半角句号后即使缺少空格（例如 `said.The`）也能正确分句；数字小数点和常见小写点连接形式不会拆分。空白片段不编号，每个网页均从 1 开始编号。句子字符区间对应清理后的网页文本，最终 quote 也从该规范化文本切片，不依赖模型复述。

模型只允许输出升序、不重复、且位于当前网页编号范围内的 JSON 整数数组。非 JSON、越界编号、倒序、重复编号或其他类型都会触发当前网页的既有重试逻辑。原始选择编号保存在 `sentence_ids`，例如：

```json
{
  "search_goal_id": "G1",
  "search_goal": "当前搜索重点",
  "web": {"web_id": "1", "web_content": "一。二。三。四。五。六。"},
  "sentences": [
    {"sentence_id": 1, "sentence": "一。"},
    {"sentence_id": 2, "sentence": "二。"},
    {"sentence_id": 3, "sentence": "三。"},
    {"sentence_id": 4, "sentence": "四。"},
    {"sentence_id": 5, "sentence": "五。"},
    {"sentence_id": 6, "sentence": "六。"}
  ],
  "sentence_ids": [3, 5],
  "quotes": ["三。四。五。"]
}
```

`sentences` 保存换行清理和规则切分后的全部非空句子及其编号，内部不再含原网页的换行符，顺序与传给模型的编号上下文完全一致；即使模型返回空数组，该字段也会完整保留。原始网页仍完整保存在 `web.web_content` 中，`sentence_ids` 只保存模型实际选中的编号。

quote 合并规则按网页顺序执行：

- 连续编号合并成一个连续 quote。
- 两个已选编号只相差 2（中间仅缺一个编号）时，自动补入中间句并合并。例如 `[3, 5]` 使用原文第 3～5 句形成一个 quote。
- 两个已选编号差值大于 2 时不补齐并拆开。例如 `[3, 6]` 形成两个 quote。
- 规则具有传递性，例如 `[1, 3, 5, 8]` 形成第 1～5 句和第 8 句两个 quote；`sentence_ids` 仍保留模型实际输出的 `[1, 3, 5, 8]`，不会改写成补齐后的编号。

编号模式复用原有逐网页执行、失败重试、诊断 messages、JSONL 逐条追加、`flush`/`fsync` 和批量编排逻辑。

### 句子起止区间方案

`sentence_ranges` 使用与编号方案完全相同的换行清理、句子切分和编号上下文，但让模型直接确定语义完整片段的闭区间边界。开始和停止编号都包含在最终 quote 中：

```text
[3, 5]|[8, 8]
```

表示抽取第 3～5 句形成第一条 quote，并将孤立的第 8 句形成第二条 quote。完全没有相关信息时输出 `[]`。

边界约束如下：

- 尽可能合并属于同一主题、事实链或解释链的信息。如果两个相关边界之间最多间隔 3 句，或只是跨过一个分段/过渡内容，Prompt 要求模型直接输出覆盖首尾的一个区间。
- 连续两句或更多句共同表达一项相关信息时，必须使用一个覆盖完整语义的区间，不能拆成相邻区间。
- 语义自足的孤立一句使用 `[n, n]`。
- 多个不连续片段使用 ASCII `|` 分隔，按开始编号升序排列。
- 区间必须位于当前网页句子范围内，满足 `start <= end`，且不得重叠。
- 模型输出的任意格式、类型、边界或顺序错误都会触发当前网页的既有重试逻辑。
- Harness 解析模型结果时会再次归并：相邻区间，或中间最多间隔 2 个句子的区间，会合并成一个更大的闭区间。该规则支持传递合并。

保存结果示例：

```json
{
  "search_goal_id": "G1",
  "search_goal": "当前搜索重点",
  "web": {"web_id": "1", "web_content": "一。二。三。四。五。"},
  "sentences": [
    {"sentence_id": 1, "sentence": "一。"},
    {"sentence_id": 2, "sentence": "二。"},
    {"sentence_id": 3, "sentence": "三。"},
    {"sentence_id": 4, "sentence": "四。"},
    {"sentence_id": 5, "sentence": "五。"}
  ],
  "sentence_ranges": [[2, 4]],
  "quotes": ["二。三。四。"]
}
```

程序先应用 Harness 的区间归并规则，再依据规范化网页文本中的句子字符区间重建 quote，不采用模型复述内容。例如模型输出 `[2, 4]|[6, 6]`，中间只隔第 5 句，最终保存为 `sentence_ranges: [[2, 6]]`，并使用第 2～6 句形成一条 quote。

区间方案测试（不调用真实模型）：

```bash
python3 -m unittest traj_web_extractor.test_sentence_range_quotes -v
```

### 原有逐字片段方案的上下文与抽取规则

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

结果文件使用 JSONL，每个目标—网页对应一行完整 JSON 对象，严格保留目标顺序和网页顺序，没有外层数组或行间逗号。`verbatim` 模式保持原有四字段；`sentence_ids` 模式额外增加 `sentences` 和 `sentence_ids`；`sentence_ranges` 模式额外增加 `sentences` 和 `sentence_ranges`。例如下面是两条原有模式的独立记录：

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
python3 -m unittest \
  traj_web_extractor.test_model_provider \
  traj_web_extractor.test_sentence_id_quotes \
  traj_web_extractor.test_jsonl_append \
  traj_web_extractor.test_pipeline -v
```

旧的严格原文校验测试与当前已注释的校验逻辑存在不一致；本次没有恢复该校验。
