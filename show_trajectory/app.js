"use strict";

/* ------------------------------------------------------------------ */
/* small DOM helpers                                                   */
/* ------------------------------------------------------------------ */

function h(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2), value);
      } else if (value !== undefined && value !== null && value !== false) {
        node.setAttribute(key, value === true ? "" : value);
      }
    }
  }
  for (const child of [].concat(children || [])) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function pre(text) {
  return h("pre", { class: "code-block" }, String(text ?? ""));
}

function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}

function copyButton(getText) {
  const btn = h("button", { class: "secondary tiny" }, "复制");
  btn.addEventListener("click", () => {
    navigator.clipboard
      .writeText(getText())
      .then(() => {
        const original = btn.textContent;
        btn.textContent = "已复制";
        setTimeout(() => (btn.textContent = original), 1200);
      })
      .catch(() => {
        btn.textContent = "复制失败";
      });
  });
  return btn;
}

function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function fmtTime(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function fmtMs(ms) {
  if (ms === null || ms === undefined) return "（无）";
  return `${(ms / 1000).toFixed(1)} s`;
}

function statusBadge(status) {
  const kind =
    {
      COMPLETED: "ok",
      CLOSED: "ok",
      MODEL_STOP: "ok",
      MAX_TURNS_REACHED: "warn",
      MAX_TURNS: "warn",
      OPEN: "info",
      UNRESOLVED: "warn",
      SOLVING: "warn",
      NON_BLOCKING: "neutral",
      UNCLASSIFIED: "neutral",
      SUPERSEDED: "neutral",
      DROPPED: "neutral",
      FAILED: "bad",
      CLOSED_BY_REFUTED: "bad",
    }[status] || "neutral";
  return h("span", { class: `badge ${kind}` }, status || "（无）");
}

function toc(label) {
  return h("h2", { id: `heading-${label.id}` }, label.text);
}

/* ------------------------------------------------------------------ */
/* app state + wiring                                                  */
/* ------------------------------------------------------------------ */

const els = {
  pathInput: document.getElementById("path-input"),
  loadBtn: document.getElementById("load-btn"),
  expandAllBtn: document.getElementById("expand-all-btn"),
  collapseAllBtn: document.getElementById("collapse-all-btn"),
  recentList: document.getElementById("recent-list"),
  status: document.getElementById("status"),
  app: document.getElementById("app"),
  toc: document.getElementById("toc"),
  tocLinks: document.getElementById("toc-links"),
};

function setStatus(text, kind) {
  els.status.textContent = text;
  els.status.className = kind || "";
}

async function loadRecent() {
  try {
    const res = await fetch("/api/list");
    const data = await res.json();
    const items = data.trajectories || [];
    els.recentList.innerHTML = "";
    if (!items.length) {
      els.recentList.appendChild(
        h("div", { class: "empty-note" }, "未在 deep_search / deep_search_claude / batch_trajectories 下发现 traj_*.json")
      );
      return;
    }
    for (const item of items) {
      const row = h(
        "div",
        { class: "recent-item" },
        [
          h("span", {}, item.path),
          h("span", { class: "meta" }, `${fmtBytes(item.size)} · ${fmtTime(item.mtime)}`),
        ]
      );
      row.addEventListener("click", () => {
        els.pathInput.value = item.path;
        loadTrajectory(item.path);
      });
      els.recentList.appendChild(row);
    }
  } catch (err) {
    els.recentList.innerHTML = "";
    els.recentList.appendChild(h("div", { class: "empty-note" }, "获取最近轨迹列表失败"));
  }
}

async function loadTrajectory(path) {
  if (!path) {
    setStatus("请输入轨迹 JSON 文件路径", "error");
    return;
  }
  setStatus("加载中…");
  els.loadBtn.disabled = true;
  try {
    const res = await fetch(`/api/load?path=${encodeURIComponent(path)}`);
    const body = await res.json();
    if (!res.ok) {
      throw new Error(body.error || `请求失败（HTTP ${res.status}）`);
    }
    setStatus(`已加载: ${body.resolved_path}`, "ok");
    render(body.data);
  } catch (err) {
    setStatus(`加载失败: ${err.message}`, "error");
    els.app.innerHTML = "";
    els.toc.hidden = true;
    els.expandAllBtn.hidden = true;
    els.collapseAllBtn.hidden = true;
  } finally {
    els.loadBtn.disabled = false;
  }
}

els.loadBtn.addEventListener("click", () => loadTrajectory(els.pathInput.value.trim()));
els.pathInput.addEventListener("keydown", (evt) => {
  if (evt.key === "Enter") loadTrajectory(els.pathInput.value.trim());
});
els.expandAllBtn.addEventListener("click", () => {
  document.querySelectorAll("#app details").forEach((d) => (d.open = true));
});
els.collapseAllBtn.addEventListener("click", () => {
  document.querySelectorAll("#app details.turn, #app details.block").forEach((d) => (d.open = false));
});

const initialPath = new URLSearchParams(window.location.search).get("path");
if (initialPath) {
  els.pathInput.value = initialPath;
  loadTrajectory(initialPath);
}
loadRecent();

/* ------------------------------------------------------------------ */
/* rendering                                                           */
/* ------------------------------------------------------------------ */

function render(data) {
  const traj = data.trajectory || data;
  const handoff = data.answer_agent_handoff ?? data.answer_handoff ?? null;

  els.app.innerHTML = "";
  els.tocLinks.innerHTML = "";
  const sections = [
    ["overview", "概览", renderOverview],
    ["metrics", "统计指标", renderMetrics],
    ["turns", "轮次（Planner / Evidence Processor）", renderTurns],
    ["goals", "搜索目标", renderGoals],
    ["evidences", "证据", renderEvidences],
    ["conflicts", "冲突", renderConflicts],
    ["webs", "检索网页", renderWebs],
    ["errors", "错误", renderErrors],
    ["handoff", "交付件 (answer_agent_handoff)", renderHandoff],
  ];

  let anySection = false;
  for (const [id, title, fn] of sections) {
    const section = fn(traj, handoff);
    if (!section) continue;
    anySection = true;
    const wrapper = h("section", { id: `section-${id}` }, [toc({ id, text: title }), section]);
    els.app.appendChild(wrapper);
    els.tocLinks.appendChild(h("a", { href: `#section-${id}` }, title));
  }

  els.toc.hidden = !anySection;
  els.expandAllBtn.hidden = !anySection;
  els.collapseAllBtn.hidden = !anySection;
}

function renderOverview(traj) {
  const fields = [
    ["原始 query", traj.user_query],
    ["当前日期", traj.current_date],
    ["run_id", traj.run_id],
    ["运行状态", statusBadge(traj.run_status)],
    ["停止原因", traj.stop_reason || "（无）"],
    ["轮次数", traj.turns ? traj.turns.length : 0],
  ];
  const grid = h("div", { class: "card overview-grid" });
  for (const [k, v] of fields) {
    grid.appendChild(
      h("div", { class: "kv" }, [
        h("div", { class: "k" }, k),
        h("div", { class: "v" }, typeof v === "string" || typeof v === "number" ? String(v) : v),
      ])
    );
  }
  return grid;
}

function renderMetrics(traj) {
  const metrics = traj.metrics;
  if (!metrics || typeof metrics !== "object") return null;
  const grid = h("div", { class: "metrics-grid" });
  const labels = {
    total_turns: "总轮次",
    search_turns: "检索轮次",
    model_call_count: "模型调用次数",
    search_action_count: "动作次数",
    search_query_count: "query 次数",
    web_result_count: "网页结果数",
    unique_web_count: "去重网页数",
    termination_type: "终止类型",
    final_action: "最终动作",
    last_model_action: "最后一次模型动作",
    elapsed_ms: "耗时",
  };
  for (const [key, label] of Object.entries(labels)) {
    if (!(key in metrics)) continue;
    const raw = metrics[key];
    const value = key === "elapsed_ms" ? fmtMs(raw) : raw === null || raw === undefined ? "（无）" : String(raw);
    grid.appendChild(
      h("div", { class: "metric-tile" }, [
        h("div", { class: "label" }, label),
        h("div", { class: "value" }, value),
      ])
    );
  }
  return h("div", {}, [
    grid,
    h("div", { class: "hint", style: "margin-top:8px" }, `开始: ${metrics.started_at || "（无）"} · 结束: ${metrics.finished_at || "（无）"}`),
  ]);
}

function messageBlock(message) {
  const role = message.role || "unknown";
  const content = message.content ?? "";
  const box = h("div", { class: "msg" });
  const head = h("div", { class: "msg-head" }, [
    h("span", {}, `role: ${role} · ${content.length.toLocaleString()} 字符`),
  ]);
  head.appendChild(copyButton(() => content));
  box.appendChild(head);
  box.appendChild(pre(content));
  return box;
}

function collapsibleBlock(title, open, bodyBuilder) {
  const body = h("div", { class: "block-body" });
  bodyBuilder(body);
  return h("details", { class: "block", open: open || undefined }, [
    h("summary", {}, title),
    body,
  ]);
}

function plannerCallBlock(title, payload) {
  if (!payload) {
    return h("div", { class: "empty-note" }, `${title}: （本轮未产生该阶段的调用，通常是因为已经 STOP）`);
  }
  return collapsibleBlock(`${title}（phase: ${payload.phase || "（无）"}）`, false, (body) => {
    const messages = payload.messages || [];
    for (const message of messages) body.appendChild(messageBlock(message));
    body.appendChild(
      collapsibleBlock("模型返回（response）", false, (respBody) => {
        const text = prettyJson(payload.response);
        const head = h("div", { style: "text-align:right;margin-bottom:4px" });
        head.appendChild(copyButton(() => text));
        respBody.appendChild(head);
        respBody.appendChild(pre(text));
      })
    );
  });
}

function evidenceProcessorBlock(execution) {
  const ep = execution.evidence_processor;
  if (!ep) return h("div", { class: "empty-note" }, "Evidence Processor: （无调用记录）");
  return collapsibleBlock("Evidence Processor 输入/输出", false, (body) => {
    const messages = ep.messages || [];
    for (const message of messages) body.appendChild(messageBlock(message));
    body.appendChild(
      collapsibleBlock("模型返回（response）", false, (respBody) => {
        const text = prettyJson(ep.response);
        const head = h("div", { style: "text-align:right;margin-bottom:4px" });
        head.appendChild(copyButton(() => text));
        respBody.appendChild(head);
        respBody.appendChild(pre(text));
      })
    );
  });
}

function conflictLine(conflict, kindLabel) {
  const parts = [
    h("strong", {}, conflict.conflict_id || "（无 id）"),
    " ",
  ];
  if (kindLabel) parts.push(h("span", { class: "badge neutral" }, kindLabel), " ");
  if (conflict.conflict_status) parts.push(statusBadge(conflict.conflict_status), " ");
  parts.push(
    h("div", {}, conflict.conflict_object || "（无描述）"),
    h(
      "div",
      { class: "hint" },
      `涉及证据: ${(conflict.conflict_evidence_ids || []).join(", ") || "（无）"}`
    )
  );
  return h("div", { style: "margin:6px 0" }, parts);
}

function renderExecution(execution) {
  const action = execution.search_action || {};
  const webs = execution.webs || [];
  const evidences = execution.evidences || [];
  const conflicts = execution.conflicts || [];
  const updatedConflicts = execution.updated_conflicts || [];
  const errors = execution.search_errors || [];

  const block = h("div", { class: "execution-block" });
  block.appendChild(
    h("div", {}, [
      h("span", { class: "action-chip" }, action.search_action || "（无动作）"),
      h("span", { class: "muted" }, ` search_goal_id: ${execution.search_goal_id || "（无）"}`),
    ])
  );
  if (action.action_reason) {
    block.appendChild(h("div", { class: "hint", style: "margin:4px 0" }, `动机: ${action.action_reason}`));
  }
  block.appendChild(
    h("div", { class: "hint" }, `query: ${(execution.search_queries || []).join(" | ") || "（无）"}`)
  );

  if (errors.length) {
    block.appendChild(
      collapsibleBlock(`检索错误（${errors.length}）`, false, (body) => {
        for (const err of errors) {
          body.appendChild(h("div", {}, `${err.query || ""} → ${err.error || ""}`));
        }
      })
    );
  }

  block.appendChild(
    collapsibleBlock(`检索到的网页（${webs.length}）`, false, (body) => {
      if (!webs.length) {
        body.appendChild(h("div", { class: "empty-note" }, "（本轮没有检索到网页）"));
        return;
      }
      for (const web of webs) body.appendChild(webItem(web));
    })
  );

  block.appendChild(evidenceProcessorBlock(execution));

  if (evidences.length || conflicts.length || updatedConflicts.length) {
    block.appendChild(
      collapsibleBlock(
        `本轮产出：证据 ${evidences.length} · 新冲突 ${conflicts.length} · 冲突补充证据 ${updatedConflicts.length}`,
        false,
        (body) => {
          for (const evidence of evidences) body.appendChild(evidenceLine(evidence));
          for (const conflict of conflicts) body.appendChild(conflictLine(conflict, "新冲突"));
          for (const conflict of updatedConflicts) body.appendChild(conflictLine(conflict, "补充证据"));
        }
      )
    );
  }

  return block;
}

function evidenceLine(evidence) {
  const quotesCount = (evidence.quotes || []).reduce((sum, g) => sum + (g.quotes || []).length, 0);
  return h("div", { style: "margin:6px 0" }, [
    h("strong", {}, evidence.evidence_id || "（无 id）"),
    h("span", { class: "muted" }, ` · ${evidence.evidence_type || ""} · ${evidence.evidence_role || ""}`),
    h("div", {}, evidence.statement || "（无陈述）"),
    h("div", { class: "hint" }, `${quotesCount} 条引用来自网页: ${(evidence.quotes || []).map((g) => g.web_id).join(", ") || "（无）"}`),
  ]);
}

function webItem(web) {
  const title = web.title || "（无标题）";
  const website = web.website || "";
  const date = web.date || "";
  const webId = web.web_id ?? "";
  const content = web.web_content || web.content || "";
  return collapsibleBlock(`[${webId}] ${title} — ${website} (${date})`, false, (body) => {
    if (web.url) {
      body.appendChild(h("div", {}, h("a", { href: web.url, target: "_blank", rel: "noopener" }, web.url)));
    }
    body.appendChild(pre(content));
  });
}

function turnBadges(turn) {
  const actions = (turn.search_actions || []).map((a) => a.search_action).filter(Boolean);
  const uniqueActions = [...new Set(actions)];
  const badges = uniqueActions.map((a) => h("span", { class: "action-chip" }, a));
  if (turn.failure) badges.push(h("span", { class: "badge bad" }, "FAILED"));
  return badges;
}

function renderTurns(traj) {
  const turns = traj.turns || [];
  if (!turns.length) return h("div", { class: "empty-note" }, "（没有轮次记录）");
  const container = h("div", {});
  turns.forEach((turn, index) => {
    const turnId = turn.turn_id ?? index + 1;
    const executions = turn.executions || [];
    const summary = h(
      "summary",
      {},
      [
        h("strong", {}, `第 ${turnId} 轮`),
        ...turnBadges(turn),
        h("span", { class: "muted" }, `${executions.length} 个动作`),
      ]
    );
    const body = h("div", { class: "turn-body" });

    body.appendChild(h("div", { style: "font-weight:600;margin:6px 0" }, "① Search Planner — 本轮动作决策"));
    body.appendChild(plannerCallBlock("planner_input", turn.planner_input));

    body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "② 执行与 Evidence Processor"));
    if (!executions.length) {
      body.appendChild(h("div", { class: "empty-note" }, "（本轮没有执行动作，通常是首轮即 STOP）"));
    } else {
      for (const execution of executions) body.appendChild(renderExecution(execution));
    }

    if (turn.state_updates && turn.state_updates.length) {
      body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "③ 状态更新（state_updates）"));
      body.appendChild(stateUpdatesTable(turn.state_updates));
    }

    body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "④ Search Planner — 状态更新与下一步"));
    body.appendChild(plannerCallBlock("planner_output", turn.planner_output));

    if (turn.next_search_actions && turn.next_search_actions.length) {
      body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "下一轮计划动作"));
      body.appendChild(nextActionsList(turn.next_search_actions));
    }

    if (turn.failure) {
      body.appendChild(
        collapsibleBlock("失败详情（failure）", true, (fBody) => {
          fBody.appendChild(pre(prettyJson(turn.failure)));
        })
      );
    }

    container.appendChild(h("details", { class: "turn", open: index === 0 || undefined }, [summary, body]));
  });
  return container;
}

function stateUpdatesTable(updates) {
  const table = h("table", {}, [
    h("thead", {}, h("tr", {}, [
      h("th", {}, "目标"), h("th", {}, "状态"), h("th", {}, "答案 / 原因"), h("th", {}, "证据缺口 / 冲突"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const update of updates) {
    tbody.appendChild(
      h("tr", {}, [
        h("td", {}, update.search_goal_id || "（无）"),
        h("td", {}, statusBadge(update.status)),
        h("td", { class: "cell-truncate" }, [
          h("div", {}, update.answer || "（无）"),
          h("div", { class: "hint" }, update.status_reason || ""),
        ]),
        h("td", { class: "cell-truncate" }, [
          h("div", {}, update.evidence_gap || "（无缺口）"),
          h("div", { class: "hint" }, update.conflict_summary || ""),
        ]),
      ])
    );
  }
  table.appendChild(tbody);
  return table;
}

function nextActionsList(actions) {
  const list = h("div");
  for (const action of actions) {
    list.appendChild(
      h("div", { style: "margin:4px 0" }, [
        h("span", { class: "action-chip" }, action.search_action || "（无）"),
        h(
          "span",
          { class: "muted" },
          ` ${action.target_goal_id || action.search_goal_id || ""}${action.target_conflict_id ? " / " + action.target_conflict_id : ""} — ${action.action_reason || ""}`
        ),
      ])
    );
  }
  return list;
}

function renderGoals(traj) {
  const goals = traj.goals || [];
  if (!goals.length) return null;
  const table = h("table", {}, [
    h("thead", {}, h("tr", {}, [
      h("th", {}, "ID"), h("th", {}, "状态"), h("th", {}, "目标"), h("th", {}, "答案"), h("th", {}, "缺口 / 冲突摘要"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const goal of goals) {
    const row = h("tr", {}, [
      h("td", {}, goal.search_goal_id),
      h("td", {}, statusBadge(goal.status)),
      h("td", { class: "cell-truncate" }, goal.search_goal),
      h("td", { class: "cell-truncate" }, goal.answer || "（无）"),
      h("td", { class: "cell-truncate" }, [
        h("div", {}, goal.evidence_gap || "（无）"),
        h("div", { class: "hint" }, goal.conflict_summary || ""),
      ]),
    ]);
    tbody.appendChild(row);
    const detailRow = h(
      "tr",
      {},
      h(
        "td",
        { colspan: "5" },
        collapsibleBlock("更多字段", false, (body) => {
          body.appendChild(pre(prettyJson(goal)));
        })
      )
    );
    tbody.appendChild(detailRow);
  }
  table.appendChild(tbody);
  return table;
}

function renderEvidences(traj) {
  const evidences = traj.evidences || [];
  if (!evidences.length) return null;
  const table = h("table", {}, [
    h("thead", {}, h("tr", {}, [
      h("th", {}, "ID"), h("th", {}, "目标"), h("th", {}, "类型"), h("th", {}, "陈述"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const evidence of evidences) {
    tbody.appendChild(
      h("tr", {}, [
        h("td", {}, evidence.evidence_id),
        h("td", {}, evidence.search_goal_id || "（无）"),
        h("td", {}, `${evidence.evidence_type || ""}`),
        h(
          "td",
          { class: "cell-truncate" },
          collapsibleBlock(evidence.statement || "（无陈述）", false, (body) => {
            body.appendChild(pre(prettyJson(evidence.quotes)));
          })
        ),
      ])
    );
  }
  table.appendChild(tbody);
  return table;
}

function renderConflicts(traj) {
  const conflicts = traj.conflicts || [];
  if (!conflicts.length) return null;
  const table = h("table", {}, [
    h("thead", {}, h("tr", {}, [
      h("th", {}, "ID"), h("th", {}, "目标"), h("th", {}, "状态"), h("th", {}, "主体"), h("th", {}, "涉及证据"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const conflict of conflicts) {
    tbody.appendChild(
      h("tr", {}, [
        h("td", {}, conflict.conflict_id),
        h("td", {}, conflict.search_goal_id || "（无）"),
        h("td", {}, conflict.conflict_status ? statusBadge(conflict.conflict_status) : "（无）"),
        h("td", { class: "cell-truncate" }, conflict.conflict_object || "（无）"),
        h("td", {}, (conflict.conflict_evidence_ids || []).join(", ") || "（无）"),
      ])
    );
  }
  table.appendChild(tbody);
  return table;
}

function renderWebs(traj) {
  const webs = traj.webs || [];
  if (!webs.length) return null;
  const wrapper = h("div");
  const filterRow = h("div", { class: "filter-row" });
  const filterInput = h("input", { type: "text", placeholder: "按标题 / 来源 / ID 过滤…" });
  filterRow.appendChild(filterInput);
  filterRow.appendChild(h("span", { class: "hint" }, ` 共 ${webs.length} 条`));
  wrapper.appendChild(filterRow);

  const list = h("div");
  wrapper.appendChild(list);

  function draw(filterText) {
    list.innerHTML = "";
    const needle = filterText.trim().toLowerCase();
    const filtered = webs.filter((web) => {
      if (!needle) return true;
      const haystack = `${web.web_id} ${web.title || ""} ${web.website || ""}`.toLowerCase();
      return haystack.includes(needle);
    });
    if (!filtered.length) {
      list.appendChild(h("div", { class: "empty-note" }, "没有匹配的网页"));
      return;
    }
    for (const web of filtered) list.appendChild(webItem(web));
  }

  filterInput.addEventListener("input", () => draw(filterInput.value));
  draw("");
  return wrapper;
}

function renderErrors(traj) {
  const errors = traj.errors || [];
  if (!errors.length) return null;
  const wrapper = h("div");
  for (const error of errors) {
    wrapper.appendChild(
      collapsibleBlock(
        `[${error.stage || "unknown"}] ${error.error_type || ""}: ${error.error || ""}`,
        true,
        (body) => {
          if (error.traceback) body.appendChild(pre(error.traceback));
          if (error.last_model_call) {
            body.appendChild(h("div", { style: "font-weight:600;margin-top:8px" }, "最近一次模型调用"));
            body.appendChild(pre(prettyJson(error.last_model_call)));
          }
        }
      )
    );
  }
  return wrapper;
}

function renderHandoff(_traj, handoff) {
  if (!handoff) return null;
  const text = prettyJson(handoff);
  const wrapper = h("div", { class: "card" });
  const head = h("div", { style: "text-align:right;margin-bottom:6px" });
  head.appendChild(copyButton(() => text));
  wrapper.appendChild(head);
  wrapper.appendChild(pre(text));
  return wrapper;
}
