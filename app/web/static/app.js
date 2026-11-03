"use strict";

/* Strata 本地控制台前端逻辑：全部走 /api/* 与 /outputs/*，无任何外部依赖。 */

const $ = (id) => document.getElementById(id);

const els = {
  health: $("health"),
  engines: $("engines"),
  form: $("task-form"),
  taskType: $("task-type"),
  engineId: $("engine-id"),
  modelId: $("model-id"),
  prompt: $("prompt"),
  parameters: $("parameters"),
  submitBtn: $("submit-btn"),
  formMessage: $("form-message"),
  statusFilter: $("status-filter"),
  refreshBtn: $("refresh-btn"),
  taskBody: $("task-body"),
  toast: $("toast"),
};

/* 引擎 ID → 支持的任务类型（与 app/engine_registration.py 保持一致）。
   未收录的引擎按“通用”处理，出现在所有类型的下拉框里。 */
const ENGINE_TASK_TYPES = {
  "flux-schnell": ["image"],
  "anima-image2video": ["video"],
};

const CANCELLABLE = new Set(["queued", "preparing", "running"]);
const RETRYABLE = new Set(["failed", "cancelled", "completed"]);

const STATUS_LABELS = {
  queued: "排队中",
  preparing: "准备中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  ready: "就绪",
  stopped: "已停止",
  starting: "启动中",
};

let enginesCache = [];
let toastTimer = null;

/* ------------------------------------------------------------------ 工具 */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message, kind) {
  els.toast.textContent = message;
  els.toast.className = "toast " + (kind === "error" ? "error" : "success");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.className = "toast hidden";
  }, 3200);
}

async function api(path, options) {
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    /* 空响应体（例如静态文件）忽略 */
  }
  if (!response.ok) {
    const message = (payload && payload.error) || `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return payload;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/* ------------------------------------------------------------------ 健康 */

async function refreshHealth() {
  try {
    const data = await api("/api/health");
    els.health.textContent = `服务正常（${data.service}）`;
    els.health.className = "health ok";
    return true;
  } catch (error) {
    els.health.textContent = "无法连接本地服务";
    els.health.className = "health bad";
    return false;
  }
}

/* ------------------------------------------------------------------ 引擎 */

function engineTaskTypes(engineId) {
  const known = ENGINE_TASK_TYPES[engineId];
  if (known) return known;
  if (engineId.includes("music") || engineId.includes("audio")) return ["music"];
  return ["image", "video", "music"];
}

function renderEngines() {
  els.engines.textContent = "";
  if (!enginesCache.length) {
    els.engines.appendChild(el("div", "muted", "未注册任何引擎（请在配置中开启 engines_enabled）"));
    renderEngineOptions();
    return;
  }
  for (const engine of enginesCache) {
    const card = el("div", "engine");
    const head = el("div", "engine-head");
    head.appendChild(el("span", "name", engine.engine_id));
    head.appendChild(el("span", "badge " + engine.state, STATUS_LABELS[engine.state] || engine.state));
    const restart = el("button", "secondary", "重启");
    restart.type = "button";
    restart.addEventListener("click", () => restartEngine(engine.engine_id));
    head.appendChild(restart);
    card.appendChild(head);
    if (engine.message) card.appendChild(el("div", "msg", engine.message));
    els.engines.appendChild(card);
  }
  renderEngineOptions();
}

function renderEngineOptions() {
  const taskType = els.taskType.value;
  const previous = els.engineId.value;
  els.engineId.textContent = "";
  const options = enginesCache.filter((engine) =>
    engineTaskTypes(engine.engine_id).includes(taskType)
  );
  for (const engine of options) {
    const option = el("option", null, engine.engine_id);
    option.value = engine.engine_id;
    els.engineId.appendChild(option);
  }
  if (options.some((engine) => engine.engine_id === previous)) {
    els.engineId.value = previous;
  }
  els.engineId.disabled = options.length === 0;
}

async function refreshEngines() {
  try {
    const data = await api("/api/engines");
    enginesCache = data.engines || [];
  } catch (error) {
    enginesCache = [];
  }
  renderEngines();
}

async function restartEngine(engineId) {
  try {
    await api(`/api/engines/${encodeURIComponent(engineId)}/restart`, { method: "POST" });
    showToast(`引擎 ${engineId} 已重启`, "success");
  } catch (error) {
    showToast(`引擎 ${engineId} 重启失败：${error.message}`, "error");
  }
  refreshEngines();
}

/* ------------------------------------------------------------------ 任务 */

function renderTasks(tasks) {
  els.taskBody.textContent = "";
  if (!tasks.length) {
    const row = el("tr");
    const cell = el("td", "muted", "暂无任务");
    cell.colSpan = 7;
    row.appendChild(cell);
    els.taskBody.appendChild(row);
    return;
  }
  for (const task of tasks) {
    els.taskBody.appendChild(renderTaskRow(task));
  }
}

function renderTaskRow(task) {
  const row = el("tr");

  /* 任务列 */
  const info = el("td");
  info.appendChild(el("div", "task-id", task.task_id));
  if (task.prompt) info.appendChild(el("div", "prompt", task.prompt));
  if (task.error_message) info.appendChild(el("div", "error", task.error_message));
  row.appendChild(info);

  /* 类型 / 引擎列 */
  const kind = el("td");
  kind.appendChild(el("div", null, { image: "图像", video: "视频", music: "音乐" }[task.task_type] || task.task_type));
  kind.appendChild(el("div", "muted", task.engine_id));
  row.appendChild(kind);

  /* 状态列 */
  const statusCell = el("td");
  statusCell.appendChild(el("span", "badge " + task.status, STATUS_LABELS[task.status] || task.status));
  row.appendChild(statusCell);

  /* 进度列 */
  const progressCell = el("td");
  if (task.progress !== null && task.progress !== undefined) {
    const ratio = task.progress <= 1 ? task.progress : task.progress / 100;
    const bar = el("div", "progress");
    const fill = el("div");
    fill.style.width = `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`;
    bar.appendChild(fill);
    progressCell.appendChild(bar);
  } else {
    progressCell.appendChild(el("span", "muted", "—"));
  }
  row.appendChild(progressCell);

  /* 创建时间列 */
  row.appendChild(el("td", null, formatTime(task.created_at)));

  /* 产物列 */
  const outputsCell = el("td");
  if (task.output_files && task.output_files.length) {
    const thumbs = el("div", "thumbs");
    for (const file of task.output_files) {
      if (!file.url) {
        thumbs.appendChild(el("a", "file", file.name));
        continue;
      }
      if (/\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name)) {
        const img = el("img");
        img.src = file.url;
        img.alt = file.name;
        img.title = file.name;
        img.loading = "lazy";
        const link = document.createElement("a");
        link.href = file.url;
        link.target = "_blank";
        link.rel = "noopener";
        link.appendChild(img);
        thumbs.appendChild(link);
      } else if (/\.(mp4|webm|mov|mkv)$/i.test(file.name)) {
        const video = el("video");
        video.src = file.url;
        video.controls = true;
        video.preload = "metadata";
        thumbs.appendChild(video);
      } else {
        const link = el("a", "file", file.name);
        link.href = file.url;
        link.target = "_blank";
        link.rel = "noopener";
        thumbs.appendChild(link);
      }
    }
    outputsCell.appendChild(thumbs);
  } else {
    outputsCell.appendChild(el("span", "muted", "—"));
  }
  row.appendChild(outputsCell);

  /* 操作列 */
  const actions = el("td");
  if (CANCELLABLE.has(task.status)) {
    const cancel = el("button", "danger", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => taskAction(task.task_id, "cancel"));
    actions.appendChild(cancel);
  }
  if (RETRYABLE.has(task.status)) {
    const retry = el("button", "secondary", "重试");
    retry.type = "button";
    retry.addEventListener("click", () => taskAction(task.task_id, "retry"));
    actions.appendChild(retry);
  }
  if (!actions.childNodes.length) actions.appendChild(el("span", "muted", "—"));
  row.appendChild(actions);

  return row;
}

async function refreshTasks() {
  const status = els.statusFilter.value;
  const path = "/api/tasks?limit=100" + (status ? `&status=${encodeURIComponent(status)}` : "");
  try {
    const data = await api(path);
    renderTasks(data.tasks || []);
  } catch (error) {
    els.taskBody.textContent = "";
    const row = el("tr");
    const cell = el("td", "error", `加载任务失败：${error.message}`);
    cell.colSpan = 7;
    row.appendChild(cell);
    els.taskBody.appendChild(row);
  }
}

async function taskAction(taskId, action) {
  try {
    await api(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, { method: "POST" });
    showToast(action === "cancel" ? "任务已取消" : "任务已重新提交", "success");
  } catch (error) {
    showToast(`${action === "cancel" ? "取消" : "重试"}失败：${error.message}`, "error");
  }
  refreshTasks();
}

/* ------------------------------------------------------------------ 提交 */

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.formMessage.textContent = "";

  let parameters = {};
  const parametersText = els.parameters.value.trim();
  if (parametersText) {
    try {
      parameters = JSON.parse(parametersText);
    } catch {
      els.formMessage.textContent = "参数必须是合法的 JSON 对象";
      els.formMessage.className = "error";
      return;
    }
    if (typeof parameters !== "object" || parameters === null || Array.isArray(parameters)) {
      els.formMessage.textContent = "参数必须是一个 JSON 对象（{…}）";
      els.formMessage.className = "error";
      return;
    }
  }

  const engineId = els.engineId.value;
  if (!engineId) {
    els.formMessage.textContent = "当前任务类型没有可用引擎，请检查配置或先重启引擎";
    els.formMessage.className = "error";
    return;
  }

  const body = {
    task_type: els.taskType.value,
    engine_id: engineId,
    prompt: els.prompt.value,
    model_id: els.modelId.value.trim() || null,
    parameters,
  };

  els.submitBtn.disabled = true;
  try {
    const record = await api("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    els.formMessage.className = "success";
    els.formMessage.textContent = `任务已提交：${record.task_id}`;
    showToast("任务已提交，进入队列", "success");
    refreshTasks();
  } catch (error) {
    els.formMessage.className = "error";
    els.formMessage.textContent = `提交失败：${error.message}`;
    showToast(`提交失败：${error.message}`, "error");
  } finally {
    els.submitBtn.disabled = false;
  }
});

els.taskType.addEventListener("change", renderEngineOptions);
els.statusFilter.addEventListener("change", refreshTasks);
els.refreshBtn.addEventListener("click", () => {
  refreshHealth();
  refreshEngines();
  refreshTasks();
});

/* ------------------------------------------------------------------ 轮询 */

async function tick() {
  if (!document.hidden) {
    await refreshHealth();
    await refreshEngines();
    await refreshTasks();
  }
}

async function init() {
  await refreshHealth();
  await refreshEngines();
  await refreshTasks();
  setInterval(tick, 3000);
}

init();
