# Deep Search 第一阶段方案 B

方案 B 是不显式维护 search goal 状态、evidence 或 conflict 的单 Agent ReAct
baseline。教师模型每轮观察全部历史搜索轨迹，然后只做两类决策：

- `SEARCH`：输出 `search_actions`，编排器执行 query 并把网页作为下一轮观察；
- `STOP`：模型判断回答原问题所需信息已经闭环。

## 运行

搜索后端仍由 `config/config.py` 中的 `search_engine` 统一配置：

```bash
python3 -m deep_search_baseline \
  --query "小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？" \
  --current-date 2026-08-09 \
  --max-turns 8 \
  --top-k 5 \
  --output deep_search_baseline/trajectories/example.json \
  --log-level DEBUG
```

教师模型使用 `get_model_infer.request_model()`，搜索使用统一的
`tools.web_search.web_search()`。

## 历史上下文

每轮上下文包含此前所有搜索轮次的：

- `search_goal`；
- `search_queries`；
- `search_errors`；
- `search_webs`。

网页进入 Prompt 时只保留 `[全局网页ID]web_content`。URL、title、content 等原始
字段只保存在 trajectory 中，不会重复占用模型上下文。

## 轨迹与指标

输出同时包含 `trajectory` 和 `answer_agent_handoff`。`trajectory.metrics` 与方案 A
使用相同口径：

- `total_turns`：实际教师决策轮数；
- `search_turns`：执行过搜索的轮数；
- `model_call_count`：教师模型调用次数；
- `search_action_count` / `search_query_count`：搜索动作和 query 数；
- `web_result_count` / `unique_web_count`：返回网页数和去重后网页数；
- `termination_type`：`MODEL_STOP`、`MAX_TURNS` 或 `FAILED`；
- `terminated_by_model` / `reached_max_turns`：两个可直接聚合的终止来源布尔值；
- `final_action`：`STOP`、`MAX_TURNS_REACHED` 或 `FAILED`；
- `elapsed_ms`：从 query 进入编排器到结束的耗时。

日志中以 `event=run_metrics` 写入同一份指标，可通过 `scheme` 区分
`scheme_a` 与 `scheme_b_baseline`。
