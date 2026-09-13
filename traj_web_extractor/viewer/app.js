"use strict";

const state = {
  payload: null,
  catalog: null,
  currentFile: null,
  activeGoalId: null,
  filter: "",
};

const byId = (id) => document.getElementById(id);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function valueOrDash(value) {
  return value === undefined || value === null || value === "" ? "—" : String(value);
}

function formatBytes(value) {
  if (!Number.isFinite(value)) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch (_) {
    return null;
  }
}

function deriveBridgedIds(selectedIds) {
  const ordered = [...new Set(selectedIds)].sort((a, b) => a - b);
  const bridged = new Set();
  if (!ordered.length) return bridged;
  let groupStart = ordered[0];
  let previous = ordered[0];
  const addRange = (start, end) => {
    for (let id = start; id <= end; id += 1) bridged.add(id);
  };
  for (let index = 1; index < ordered.length; index += 1) {
    const current = ordered[index];
    if (current - previous > 2) {
      addRange(groupStart, previous);
      groupStart = current;
    }
    previous = current;
  }
  addRange(groupStart, previous);
  ordered.forEach((id) => bridged.delete(id));
  return bridged;
}

function selectedIdsForRecord(record) {
  const selected = new Set(record.sentence_ids || []);
  (record.sentence_ranges || []).forEach(([start, end]) => {
    for (let id = start; id <= end; id += 1) selected.add(id);
  });
  return selected;
}

function metadataRow(list, label, value, options = {}) {
  list.append(element("dt", "", label));
  const data = element("dd");
  if (options.url) {
    const safeUrl = safeHttpUrl(options.url);
    if (safeUrl) {
      const link = element("a", "", valueOrDash(value));
      link.href = safeUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      data.append(link);
    } else {
      data.textContent = valueOrDash(value);
    }
  } else {
    data.textContent = valueOrDash(value);
  }
  list.append(data);
}

function searchableText(record) {
  const web = record.web || {};
  return [
    web.title, web.website, web.url, web.web_id, web.id, web.source_id,
    web.date, web.web_content,
    ...record.sentences.map((item) => item.sentence),
    ...record.quotes,
  ].filter(Boolean).join("\n").toLocaleLowerCase();
}

function renderSentencePanel(record) {
  const panel = element("section", "sentence-panel");
  const heading = element("div", "section-heading");
  heading.append(
    element("h4", "", "拆分句子与编号"),
    element("span", "", `${record.sentences.length} 句`),
  );
  panel.append(heading);

  if (!record.sentences.length) {
    panel.append(element("p", "empty-state", "该记录没有保存句子编号数据。"));
    return panel;
  }

  const selected = selectedIdsForRecord(record);
  const bridged = record.selection_mode === "sentence_ranges"
    ? new Set()
    : deriveBridgedIds(record.sentence_ids);
  const list = element("div", "sentence-list");
  record.sentences.forEach((item) => {
    let className = "sentence-row";
    let status = "未抽取";
    if (selected.has(item.sentence_id)) {
      className += " selected";
      status = "模型选中";
    } else if (bridged.has(item.sentence_id)) {
      className += " bridged";
      status = "规则补全";
    }
    const row = element("div", className);
    row.title = status;
    row.append(
      element("span", "sentence-id", `[${item.sentence_id}]`),
      element("p", "sentence-text", item.sentence),
    );
    list.append(row);
  });
  panel.append(list);
  return panel;
}

function renderExtractionPanel(record) {
  const web = record.web || {};
  const panel = element("aside", "extraction-panel");

  const info = element("section", "subsection");
  info.append(element("h4", "", "Web 信息"));
  const metadata = element("dl", "metadata");
  metadataRow(metadata, "web_id", web.web_id ?? web.id);
  metadataRow(metadata, "source_id", web.source_id);
  metadataRow(metadata, "website", web.website);
  metadataRow(metadata, "date", web.date);
  metadataRow(metadata, "title", web.title);
  metadataRow(metadata, "url", web.url, { url: web.url });
  info.append(metadata);
  panel.append(info);

  const ids = element("section", "subsection");
  const usesRanges = record.selection_mode === "sentence_ranges";
  const selections = usesRanges ? record.sentence_ranges : record.sentence_ids;
  ids.append(element(
    "h4",
    "",
    usesRanges ? `模型抽取区间 · ${selections.length}` : `模型抽取 ID · ${selections.length}`,
  ));
  if (selections.length) {
    const idList = element("div", "id-list");
    selections.forEach((item) => {
      const label = usesRanges ? `[${item[0]}, ${item[1]}]` : item;
      idList.append(element("span", "id-chip", label));
    });
    ids.append(idList);
  } else {
    ids.append(element("p", "empty-state", usesRanges ? "模型未选择句子区间。" : "模型未选中句子。"));
  }
  panel.append(ids);

  const quotes = element("section", "subsection");
  quotes.append(element("h4", "", `最终 Quotes · ${record.quotes.length}`));
  if (record.quotes.length) {
    record.quotes.forEach((quote) => quotes.append(element("blockquote", "quote-card", quote)));
  } else {
    quotes.append(element("p", "empty-state", "该网页未抽取出相关 quote。"));
  }
  panel.append(quotes);

  const raw = element("details", "raw-web");
  raw.append(element("summary", "", "查看原始 web_content"));
  raw.append(element("pre", "", valueOrDash(web.web_content)));
  panel.append(raw);
  return panel;
}

function renderWebCard(record, visibleIndex, openByDefault) {
  const web = record.web || {};
  const card = element("details", "web-card");
  card.open = openByDefault;

  const summary = element("summary", "web-summary");
  summary.append(element("span", "web-index", `WEB ${String(visibleIndex).padStart(2, "0")}`));
  const heading = element("div", "web-heading");
  heading.append(
    element("h3", "", valueOrDash(web.title)),
    element("p", "", [web.website, web.date, `ID ${valueOrDash(web.web_id ?? web.id)}`].filter(Boolean).join(" · ")),
  );
  summary.append(heading);
  const badges = element("div", "summary-badges");
  const usesRanges = record.selection_mode === "sentence_ranges";
  const selectionCount = usesRanges ? record.sentence_ranges.length : record.sentence_ids.length;
  const selectionLabel = usesRanges ? "ranges" : "IDs";
  badges.append(element("span", `mini-badge ${selectionCount ? "has-quotes" : "empty"}`, `${selectionCount} ${selectionLabel}`));
  badges.append(element("span", `mini-badge ${record.quotes.length ? "has-quotes" : "empty"}`, `${record.quotes.length} quotes`));
  summary.append(badges);
  card.append(summary);

  const body = element("div", "web-body");
  const context = element("div", "context-strip");
  const query = element("div");
  query.append(element("span", "field-label", "用户 QUERY"), element("p", "", state.payload.user_query));
  const goal = element("div");
  goal.append(element("span", "field-label", "当前 SEARCH GOAL"), element("p", "", record.search_goal));
  context.append(query, goal);
  body.append(context);

  const grid = element("div", "web-content-grid");
  grid.append(renderSentencePanel(record), renderExtractionPanel(record));
  body.append(grid);
  card.append(body);
  return card;
}

function renderGoalNav() {
  const list = byId("goal-nav-list");
  list.replaceChildren();
  state.payload.goals.forEach((goal) => {
    const button = element("button", "goal-nav-button");
    button.type = "button";
    button.dataset.goalId = goal.search_goal_id;
    button.classList.toggle("active", goal.search_goal_id === state.activeGoalId);
    button.setAttribute("aria-pressed", goal.search_goal_id === state.activeGoalId ? "true" : "false");
    button.append(
      element("span", "nav-goal-id", goal.search_goal_id),
      element("span", "nav-goal-label", goal.search_goal),
      element("span", "nav-goal-count", goal.web_count),
    );
    button.addEventListener("click", () => activateGoal(goal.search_goal_id));
    list.append(button);
  });
}

function renderActiveGoal() {
  const goal = state.payload.goals.find((item) => item.search_goal_id === state.activeGoalId);
  if (!goal) return;
  const position = state.payload.goals.indexOf(goal) + 1;
  byId("active-goal-id").textContent = goal.search_goal_id;
  byId("goal-position").textContent = `目标 ${position} / ${state.payload.goals.length}`;
  byId("active-goal-title").textContent = goal.search_goal;

  const metrics = byId("goal-metrics");
  metrics.replaceChildren();
  [
    [goal.web_count, "个网页"],
    [goal.sentence_count, "个句子"],
    [
      goal.uses_sentence_ranges ? goal.range_count : goal.selected_sentence_count,
      goal.uses_sentence_ranges ? "个抽取区间" : "个选中 ID",
    ],
    [goal.quote_count, "条 Quotes"],
  ].forEach(([number, label]) => {
    const item = element("span");
    item.append(element("strong", "", number), document.createTextNode(` ${label}`));
    metrics.append(item);
  });

  const needle = state.filter.trim().toLocaleLowerCase();
  const records = needle
    ? goal.records.filter((record) => searchableText(record).includes(needle))
    : goal.records;
  byId("result-count").textContent = `${records.length} / ${goal.records.length} 个网页`;
  const webList = byId("web-list");
  webList.replaceChildren();
  if (!records.length) {
    webList.append(element("div", "no-results", "当前 search goal 中没有匹配的网页。"));
    return;
  }
  records.forEach((record, index) => webList.append(renderWebCard(record, index + 1, index === 0)));
}

function activateGoal(goalId) {
  state.activeGoalId = goalId;
  state.filter = "";
  byId("web-filter").value = "";
  renderGoalNav();
  renderActiveGoal();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderApp(payload) {
  state.payload = payload;
  state.activeGoalId = payload.goals.length ? payload.goals[0].search_goal_id : null;
  byId("source-name").textContent = payload.source_name;
  byId("source-path").textContent = payload.source_file;
  byId("user-query").textContent = payload.user_query;
  byId("stat-goals").textContent = payload.summary.goal_count;
  byId("stat-webs").textContent = payload.summary.web_count;
  byId("stat-quotes").textContent = payload.summary.quote_count;
  byId("goal-count").textContent = `${payload.goals.length} TOTAL`;

  const warnings = byId("warning-list");
  warnings.hidden = true;
  warnings.replaceChildren();
  if (payload.warnings.length) {
    warnings.hidden = false;
    warnings.replaceChildren(...payload.warnings.map((message) => element("div", "", `注意：${message}`)));
  }

  renderGoalNav();
  if (state.activeGoalId) {
    renderActiveGoal();
  } else {
    byId("active-goal-id").textContent = "—";
    byId("goal-position").textContent = "";
    byId("active-goal-title").textContent = "该文件没有可展示的抽取记录";
    byId("goal-metrics").replaceChildren();
    byId("result-count").textContent = "0 个网页";
    byId("web-list").replaceChildren(element("div", "no-results", "该文件没有可展示的抽取记录。"));
  }
  byId("loading-screen").hidden = true;
  byId("app-shell").hidden = false;
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (response.ok) return response.json();
  let message = `接口返回 ${response.status}`;
  try {
    const body = await response.json();
    if (typeof body.error === "string" && body.error) message = body.error;
  } catch (_) {
    // Keep the status-based fallback when an error response is not JSON.
  }
  throw new Error(message);
}

function renderFileCatalog(catalog) {
  state.catalog = catalog;
  const select = byId("result-file");
  select.replaceChildren();
  catalog.files.forEach((file) => {
    const option = element("option");
    option.value = file.name;
    const size = formatBytes(file.size_bytes);
    option.textContent = size ? `${file.name} · ${size}` : file.name;
    option.title = file.name;
    select.append(option);
  });
  select.disabled = !catalog.files.length;
  if (catalog.selected_file) select.value = catalog.selected_file;
  byId("file-count").textContent = `${catalog.files.length} FILES`;
}

async function loadFile(fileName, { initial = false } = {}) {
  const select = byId("result-file");
  const status = byId("file-load-status");
  const workspace = document.querySelector(".workspace");
  const previousFile = state.currentFile;
  select.disabled = true;
  status.classList.remove("error");
  status.textContent = "正在解析文件…";
  workspace.setAttribute("aria-busy", "true");
  try {
    const payload = await fetchJson(`/api/data?file=${encodeURIComponent(fileName)}`);
    state.currentFile = fileName;
    renderApp(payload);
    select.value = fileName;
    status.textContent = `已加载 · ${payload.summary.web_count} 个网页`;
  } catch (error) {
    if (initial) throw error;
    select.value = previousFile || "";
    status.classList.add("error");
    status.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    select.disabled = !state.catalog || !state.catalog.files.length;
    workspace.removeAttribute("aria-busy");
  }
}

async function initialize() {
  const catalog = await fetchJson("/api/files");
  renderFileCatalog(catalog);
  if (!catalog.selected_file) {
    throw new Error("扫描目录中没有可加载的 JSONL 文件。");
  }
  await loadFile(catalog.selected_file, { initial: true });
}

function showFatalError(error) {
  byId("loading-screen").hidden = true;
  byId("fatal-error-message").textContent = error instanceof Error ? error.message : String(error);
  byId("fatal-error").hidden = false;
}

byId("web-filter").addEventListener("input", (event) => {
  state.filter = event.target.value;
  renderActiveGoal();
});

byId("expand-all").addEventListener("click", () => {
  document.querySelectorAll(".web-card").forEach((card) => { card.open = true; });
});

byId("collapse-all").addEventListener("click", () => {
  document.querySelectorAll(".web-card").forEach((card) => { card.open = false; });
});

byId("result-file").addEventListener("change", (event) => {
  if (event.target.value && event.target.value !== state.currentFile) {
    loadFile(event.target.value);
  }
});

initialize().catch(showFatalError);
