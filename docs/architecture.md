# 第一阶段架构与协议

## 数据流

```text
user_query
   │
   ▼
Search Planner（首轮 INIT_GOAL）
   │ search_actions + search_queries
   ▼
Orchestrator ──逐 query──► web_search
   │                         │ references
   │◄────────────────────────┘
   │ 每个目标、本轮网页、历史状态/证据摘要
   ▼
Evidence Processor
   │ evidences + conflicts
   ▼
Orchestrator（统一编号、校验引用、写入状态）
   │ 搜索状态 + 本轮信息
   ▼
Search Planner（状态更新 + 下一轮动作）
   │
   ├─ STOP ─► trajectory + answer_agent_handoff
   └─ 其他动作 ─► 下一轮搜索
```

## 上下文边界

### Search Planner 首轮

输入仅包含 `user_query`、`current_date` 和首轮任务说明。输出必须恰好包含一个
`INIT_GOAL`，避免在没有事实锚点时一次性展开大量互相依赖的目标。首轮使用独立的
system prompt，顶层只允许输出 `search_actions`；输出必须是标准 JSON，不允许代码块、
注释、解释性前后缀、单引号或尾随逗号。

### Evidence Processor

输入包含：

- 原始 query 和当前日期；
- 单个当前目标；
- 该目标已有答案、事实陈述、冲突和证据缺口；
- 该目标过去抽取的 evidence（不包含历史网页全文）；
- 本轮网页；
- 下一可用 evidence/conflict 编号。

新增 quote 只允许引用本轮网页。编排器会再次校验引用，防止教师模型输出不存在
的 `web_id`。

传给模型的本轮网页仅包含全局网页 ID 和搜索适配器预先组装的 `web_content`；URL、
标题、正文等原始字段不会再次重复填充。完整网页对象只保留在轨迹状态中。

### Search Planner 非首轮

非首轮使用另一套独立 system prompt，顶层必须同时输出 `search_state_updates` 和
`next_turn_search_actions`。输入在 `constant_info` 与 `search_status` 之间包含按轮次组织的
`search_history`，记录历史 search goal、action reason、query 和网页，使 Planner 能重建
此前的搜索行为并避免重复检索；随后提供紧凑的 `search_status`，以及本轮每个目标的
query、搜索异常、网页、证据和冲突。Planner 先更新本轮涉及的目标，再规划下一轮；模型
判断闭环时，下一轮动作只能包含一个 `STOP`。两种分支都必须输出可被标准解析器直接
解析的 JSON。

`search_status` 不再复制全量搜索状态：

- 尚未经过 Planner 状态判定的新目标只提供 `search_goal_id`、`search_goal` 和
  `in_progress: true`，不提前分配 `OPEN` 等正式状态；
- 已经过 Planner 判定的历史目标只提供 `search_goal_id`、`search_goal`、`status`、
  `answer`、`conflict_summary` 和 `evidence_gap`；
- 不提供 evidence ID、conflict ID、依赖 ID 或事实陈述。本轮可引用的 ID 仅来自
  `current_turn_info`。

网页在轨迹中保留全文，但送入模型时只提供 ID 与 `web_content`，单篇默认最多 6000
字，避免上下文无界增长。

## 状态机约束

- 新建目标默认为 `OPEN`。
- `CONTINUE_SEARCH` 和 `VERIFY_CONFLICT` 只能指向 `OPEN` 目标。
- `REFINE_GOAL` 创建新目标，并将原目标置为 `SUPERSEDED`。
- `CLOSED` 必须引用至少一个已存在 evidence，且不能保留阻塞 conflict。
- 存在任一 `OPEN` 目标时禁止 `STOP`。
- 模型产生的局部 ID 会由编排器归一化为全局连续 `E1...`、`C1...`、网页
  `1...`，避免多目标或多轮碰撞。

## 容错与轨迹完整性

- 单条 search query 失败时记录 `search_errors`，同一动作的其他 query 继续执行。
- 教师输出若不是 JSON 对象、动作非法或引用不存在的 ID，则抛出 `ProtocolError`，
  防止污染训练数据。
- 每轮结束进行一次原子 checkpoint；进程中断不会留下半个 JSON 文件。
- 协议异常会将运行标记为 `FAILED`，保存 traceback 与异常前最后一次模型响应，
  然后再向调用方抛出异常。
- 控制台日志用于观察进度，轮转 JSON Lines 日志可按 `run_id`、`turn_id`、
  `search_goal_id`、`agent`、`stage` 和 `event` 过滤。
- 达到最大轮数时状态为 `MAX_TURNS_REACHED`，已有轨迹与 Answer Agent handoff
  仍会保存。

## 第二阶段摊平建议

完整轨迹已经保留摊平所需的顺序信息。后续可按每轮构造单 Agent 训练序列：

1. Planner 的 `search_actions`；
2. 搜索工具调用与网页 observation；
3. Evidence Processor 的 `evidences/conflicts` 转写为单 Agent 观察动作；
4. Planner 的 `search_state_updates`；
5. `next_search_actions`。

摊平时应保留全局 ID，不要重新编号，否则状态更新中的 evidence/conflict 引用会失效。

## 方案 A/B 通用监控

两个方案的 trajectory 顶层状态中都包含同结构的 `metrics`，日志也都会输出
`event=run_metrics`：

- 轮次：`total_turns`、`search_turns`；
- 调用量：`model_call_count`、`search_action_count`、`search_query_count`；
- 网页量：`web_result_count`、`unique_web_count`；
- 终止：`termination_type`、`terminated_by_model`、`reached_max_turns`、
  `final_action`；
- 时延：`started_at`、`finished_at`、`elapsed_ms`。

方案 A 的 `total_turns` 对应其搜索编排 turn；方案 B 的 `total_turns` 对应单 Agent
教师决策 turn（包括最终只输出 STOP 的一轮）。模型调用开销应使用
`model_call_count` 做跨方案比较。
