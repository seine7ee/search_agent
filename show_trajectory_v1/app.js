"use strict";

/* ------------------------------------------------------------------ */
/* This is show_trajectory's viewer adapted for the single-agent /     */
/* two-agent trajectory schema (deep_search_single_agent /             */
/* deep_search_multi_agent) instead of deep_search_claude's:           */
/*   - "rounds" (round_id/kind: init|turn) instead of "turns"          */
/*   - one search_state_update object per round instead of a list      */
/*   - a round's model call(s) is either "model_call" (single_agent)   */
/*     or "evidence_extractor_call" + "planner_call" (multi_agent);    */
/*     which one is present is auto-detected per round, so the same    */
/*     viewer renders trajectories from either scheme.                 */
/*   - goal status vocabulary OPEN/CLOSED/REFUTED/UNRESOLVED/SUPERSEDED*/
/*   - evidence/conflict field names: evidence_summary,                */
/*     conflict_object + conflict_statement + conflicted_evidence_ids  */
/* ------------------------------------------------------------------ */

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
      MAX_ROUNDS_REACHED: "warn",
      MAX_ROUNDS: "warn",
      OPEN: "info",
      UNRESOLVED: "warn",
      SUPERSEDED: "neutral",
      FAILED: "bad",
      REFUTED: "bad",
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
        h(
          "div",
          { class: "empty-note" },
          "未在 deep_search_single_agent / deep_search_multi_agent / batch_trajectories 下发现 traj_*.json"
        )
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
    ["rounds", "轮次（Search Agent / Evidence Extractor + Planner）", renderRounds],
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
    ["运行方案 (scheme)", (traj.metrics && traj.metrics.scheme) || "（无）"],
    ["运行状态", statusBadge(traj.run_status)],
    ["停止原因", traj.stop_reason || "（无）"],
    ["轮次数", traj.rounds ? traj.rounds.length : 0],
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
    total_rounds: "总轮次",
    search_rounds: "检索轮次",
    model_call_count: "模型调用次数（总）",
    planner_call_count: "Planner 调用次数",
    evidence_extractor_call_count: "Evidence Extractor 调用次数",
    search_query_count: "query 次数",
    web_result_count: "网页结果数",
    unique_web_count: "去重网页数",
    goal_created_count: "创建目标数",
    conflict_search_count: "冲突核实次数",
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

function modelCallBlock(title, payload) {
  if (!payload) {
    return h("div", { class: "empty-note" }, `${title}: （无调用记录）`);
  }
  return collapsibleBlock(title, false, (body) => {
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

/**
 * A round's model call(s) can be either a single "model_call" (the
 * single_agent scheme, one call per round) or "evidence_extractor_call" +
 * "planner_call" (the multi_agent scheme, two calls per round). Detecting
 * this per round -- rather than globally from traj.metrics.scheme -- lets
 * the same viewer render either scheme's trajectory without branching
 * elsewhere in the code.
 */
function roundModelCalls(round) {
  if (round.model_call) {
    return [["Search Agent 调用", round.model_call]];
  }
  const calls = [];
  if (round.evidence_extractor_call) calls.push(["Evidence Extractor 调用", round.evidence_extractor_call]);
  if (round.planner_call) calls.push(["Planner 调用", round.planner_call]);
  return calls;
}

function actionChip(action) {
  if (!action) return h("span", { class: "muted" }, "（无动作）");
  const parts = [h("span", { class: "action-chip" }, action.search_action || "（无）")];
  if (action.type) parts.push(h("span", { class: "muted" }, ` type=${action.type}`));
  const target = action.target_id;
  if (target) parts.push(h("span", { class: "muted" }, ` target=${target}`));
  if (action.search_goal_id) parts.push(h("span", { class: "muted" }, ` search_goal_id=${action.search_goal_id}`));
  if (action.supersedes_goal_id) parts.push(h("span", { class: "muted" }, ` supersedes=${action.supersedes_goal_id}`));
  return h("span", {}, parts);
}

function actionDetail(action) {
  if (!action) return h("div", { class: "empty-note" }, "（无动作）");
  const box = h("div", {});
  box.appendChild(h("div", {}, actionChip(action)));
  if (action.action_reason) {
    box.appendChild(h("div", { class: "hint", style: "margin:4px 0" }, `理由: ${action.action_reason}`));
  }
  if (action.search_goal) {
    box.appendChild(h("div", {}, `新目标: ${action.search_goal}`));
  }
  if (action.search_focus) {
    box.appendChild(h("div", { class: "hint" }, `search_focus: ${action.search_focus}`));
  }
  if (action.depends_on_goal_ids && action.depends_on_goal_ids.length) {
    box.appendChild(h("div", { class: "hint" }, `depends_on_goal_ids: ${action.depends_on_goal_ids.join(", ")}`));
  }
  if (action.search_queries && action.search_queries.length) {
    box.appendChild(
      h("div", { class: "hint" }, `search_queries: ${action.search_queries.join(" | ")}`)
    );
  }
  return box;
}

function evidenceLine(evidence) {
  const quotesCount = (evidence.quotes || []).reduce((sum, g) => sum + (g.quotes || []).length, 0);
  return h("div", { style: "margin:6px 0" }, [
    h("strong", {}, evidence.evidence_id || "（无 id）"),
    h("span", { class: "muted" }, evidence.evidence_summary ? ` · ${evidence.evidence_summary}` : ""),
    h("div", {}, evidence.statement || "（无陈述）"),
    h("div", { class: "hint" }, `${quotesCount} 条引用来自网页: ${(evidence.quotes || []).map((g) => g.web_id).join(", ") || "（无）"}`),
  ]);
}

function conflictLine(conflict) {
  return h("div", { style: "margin:6px 0" }, [
    h("strong", {}, conflict.conflict_id || "（无 id）"),
    h("span", { class: "muted" }, conflict.conflict_object ? ` · ${conflict.conflict_object}` : ""),
    h("div", {}, conflict.conflict_statement || "（无描述）"),
    h(
      "div",
      { class: "hint" },
      `涉及证据: ${(conflict.conflicted_evidence_ids || []).join(", ") || "（无）"}`
    ),
  ]);
}

function stateUpdateCard(update) {
  if (!update) return h("div", { class: "empty-note" }, "（本轮未产出状态更新）");
  const grid = h("div", { class: "card overview-grid" });
  const fields = [
    ["search_goal_id", update.search_goal_id],
    ["status", statusBadge(update.status)],
    ["supported_evidence_ids", (update.supported_evidence_ids || []).join(", ") || "（无）"],
    ["blocked_conflict_ids", (update.blocked_conflict_ids || []).join(", ") || "（无）"],
    ["search_round_count / max_searches", `${update.search_round_count ?? "?"} / ${update.max_searches ?? "?"}`],
  ];
  for (const [k, v] of fields) {
    grid.appendChild(
      h("div", { class: "kv" }, [
        h("div", { class: "k" }, k),
        h("div", { class: "v" }, typeof v === "string" || typeof v === "number" ? String(v) : v),
      ])
    );
  }
  const box = h("div", {}, [grid]);
  if (update.supported_statement) {
    box.appendChild(h("div", { style: "margin-top:8px" }, [h("strong", {}, "supported_statement: "), update.supported_statement]));
  }
  if (update.info_gap) {
    box.appendChild(h("div", { class: "hint", style: "margin-top:4px" }, `info_gap: ${update.info_gap}`));
  }
  return box;
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

function roundBadges(round) {
  const badges = [];
  const action = round.action || {};
  if (action.search_action) badges.push(h("span", { class: "action-chip" }, action.search_action));
  if (round.kind) badges.push(h("span", { class: "badge neutral" }, round.kind));
  if (round.failure) badges.push(h("span", { class: "badge bad" }, "FAILED"));
  return badges;
}

function renderRounds(traj) {
  const rounds = traj.rounds || [];
  if (!rounds.length) return h("div", { class: "empty-note" }, "（没有轮次记录）");
  const container = h("div", {});
  rounds.forEach((round, index) => {
    const roundId = round.round_id ?? index + 1;
    const summary = h(
      "summary",
      {},
      [
        h("strong", {}, `第 ${roundId} 轮`),
        ...roundBadges(round),
        round.target_goal_id
          ? h("span", { class: "muted" }, `目标: ${round.target_goal_id}${round.target_conflict_id ? ` / ${round.target_conflict_id}` : ""}`)
          : null,
      ].filter(Boolean)
    );
    const body = h("div", { class: "turn-body" });

    if (round.kind === "init") {
      body.appendChild(h("div", { style: "font-weight:600;margin:6px 0" }, "① 创建首个搜索目标（CREATE_GOAL）"));
      body.appendChild(actionDetail(round.action));
    } else {
      body.appendChild(
        h(
          "div",
          { class: "hint", style: "margin:6px 0" },
          `本轮处理的观测来自上一轮的动作，针对: ${round.target_goal_text || round.target_goal_id || "（无）"}`
        )
      );
      if (round.search_queries && round.search_queries.length) {
        body.appendChild(h("div", { class: "hint" }, `search_queries: ${round.search_queries.join(" | ")}`));
      }
    }

    body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "② 模型调用"));
    for (const [title, payload] of roundModelCalls(round)) {
      body.appendChild(modelCallBlock(title, payload));
    }

    if (round.kind === "init" && round.execution) {
      body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "③ 检索到的网页"));
      const webs = round.execution.webs || [];
      body.appendChild(
        collapsibleBlock(`本轮检索到的网页（${webs.length}）`, false, (webBody) => {
          if (!webs.length) {
            webBody.appendChild(h("div", { class: "empty-note" }, "（本轮没有检索到网页）"));
            return;
          }
          for (const web of webs) webBody.appendChild(webItem(web));
        })
      );
    }

    const evidences = round.evidences || [];
    const conflicts = round.conflicts || [];
    if (round.kind === "turn") {
      body.appendChild(
        h(
          "div",
          { style: "font-weight:600;margin:14px 0 6px" },
          `③ 本轮产出：证据 ${evidences.length} · 冲突 ${conflicts.length}`
        )
      );
      if (!evidences.length && !conflicts.length) {
        body.appendChild(h("div", { class: "empty-note" }, "（本轮未产生新证据或冲突）"));
      } else {
        for (const evidence of evidences) body.appendChild(evidenceLine(evidence));
        for (const conflict of conflicts) body.appendChild(conflictLine(conflict));
      }

      body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "④ 状态更新（search_state_update）"));
      body.appendChild(stateUpdateCard(round.state_update));

      body.appendChild(h("div", { style: "font-weight:600;margin:14px 0 6px" }, "⑤ 下一步动作（action）"));
      body.appendChild(actionDetail(round.action));
    }

    if (round.failure) {
      body.appendChild(
        collapsibleBlock("失败详情（failure）", true, (fBody) => {
          fBody.appendChild(pre(prettyJson(round.failure)));
        })
      );
    }

    container.appendChild(h("details", { class: "turn", open: index === 0 || undefined }, [summary, body]));
  });
  return container;
}

function renderGoals(traj) {
  const goals = traj.goals || [];
  if (!goals.length) return null;
  const table = h("table", {}, [
    h("thead", {}, h("tr", {}, [
      h("th", {}, "ID"), h("th", {}, "状态"), h("th", {}, "目标"), h("th", {}, "结论"), h("th", {}, "信息缺口 / 阻塞冲突"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const goal of goals) {
    const row = h("tr", {}, [
      h("td", {}, goal.search_goal_id),
      h("td", {}, statusBadge(goal.status)),
      h("td", { class: "cell-truncate" }, goal.search_goal),
      h("td", { class: "cell-truncate" }, goal.supported_statement || "（无）"),
      h("td", { class: "cell-truncate" }, [
        h("div", {}, goal.info_gap || "（无）"),
        h("div", { class: "hint" }, (goal.blocked_conflict_ids || []).join(", ") || ""),
      ]),
    ]);
    tbody.appendChild(row);
    const detailRow = h(
      "tr",
      {},
      h(
        "td",
        { colspan: "5" },
        collapsibleBlock("更多字段（预算 / 派生关系 / 依赖 / query 等）", false, (body) => {
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

  // Evidence objects don't carry their own search_goal_id -- derive it from
  // each goal's evidence_ids list so the table can still show ownership.
  const goalByEvidenceId = {};
  for (const goal of traj.goals || []) {
    for (const evidenceId of goal.evidence_ids || []) {
      goalByEvidenceId[evidenceId] = goal.search_goal_id;
    }
  }

  const table = h("table", {}, [
    h("thead", {}, h("tr", {}, [
      h("th", {}, "ID"), h("th", {}, "所属目标"), h("th", {}, "摘要"), h("th", {}, "陈述"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const evidence of evidences) {
    tbody.appendChild(
      h("tr", {}, [
        h("td", {}, evidence.evidence_id),
        h("td", {}, goalByEvidenceId[evidence.evidence_id] || "（无）"),
        h("td", { class: "cell-truncate" }, evidence.evidence_summary || "（无）"),
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
      h("th", {}, "ID"), h("th", {}, "目标"), h("th", {}, "冲突对象"), h("th", {}, "描述"), h("th", {}, "涉及证据"),
    ])),
  ]);
  const tbody = h("tbody");
  for (const conflict of conflicts) {
    tbody.appendChild(
      h("tr", {}, [
        h("td", {}, conflict.conflict_id),
        h("td", {}, conflict.search_goal_id || "（无）"),
        h("td", { class: "cell-truncate" }, conflict.conflict_object || "（无）"),
        h("td", { class: "cell-truncate" }, conflict.conflict_statement || "（无）"),
        h("td", {}, (conflict.conflicted_evidence_ids || []).join(", ") || "（无）"),
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
