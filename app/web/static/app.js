"use strict";

/* 辰鹄AI创作中心 前端 —— Midjourney Web 式交互，全部走现有 /api/* 与 /outputs/*。
 * 零外部依赖、离线可用；会话与参数保存在浏览器 localStorage，任务本体仍由后端 SQLite 队列管理。
 * V（Variation）/ U（Upscale）通过现有 img2img 参数（init_image_path + strength）实现，后端未改动。 */

const $ = (id) => document.getElementById(id);

const els = {
  feed: $("feed"), feedEmpty: $("feed-empty"),
  modeTabs: $("mode-tabs"),
  sessionList: $("session-list"), sessionTitle: $("session-title"),
  prompt: $("prompt-input"), imagineBtn: $("imagine-btn"), composerMeta: $("composer-meta"),
  refChip: $("ref-chip"), composer: $("composer"),
  refZone: $("ref-zone"), refZoneText: $("ref-zone-text"), pickRef: $("pick-ref"), fileInput: $("file-input"),
  enginePill: $("engine-pill"), healthPill: $("health-pill"),
  engineSelect: $("engine-select"), engineState: $("engine-state"), engineRestart: $("engine-restart"),
  toast: $("toast"),
};

const LS = {
  sessions: "strata.mj.sessions.v2",
  settings: "strata.mj.settings.v2",
  ui: "strata.mj.ui.v2",
  active: "strata.mj.active.v2",
};

/* 引擎 → 支持的任务类型（与 app/engine_registration.py 一致）。 */
const ENGINE_TASK_TYPES = { "flux-schnell": ["image"], "anima-image2video": ["video"], "ace-step-15": ["music"] };
/* 后端参数白名单（app/tasks/flux_worker.py _ALLOWED_PARAMS）。 */
const ALLOWED_PARAMS = new Set([
  "width", "height", "steps", "seed", "negative_prompt",
  "init_image_path", "strength",
]);
const ENGINE_STATE_LABELS = { ready: "就绪", stopped: "未启动", starting: "启动中", failed: "异常", idle: "空闲" };
/* 12GB 显存的像素安全线：超过则提示（后端不强限）。 */
const PIXEL_SAFE = 1_350_000;
/* AI 音乐（ACE-Step 1.5）最低显存门槛（MB）：不足则前端直接拦截提交。 */
const MUSIC_MIN_VRAM_MB = 12288;

/* 模式 Tab = 前端路由；后端接口按 task_type（image/video/music）分开，未改动。 */
const MODES = ["t2i", "i2i", "t2v", "i2v", "music"];
const MODALITY = { t2i: "image", i2i: "image", t2v: "video", i2v: "video", music: "music" };
const MODE_LABELS = { t2i: "文生图", i2i: "图生图", t2v: "文生视频", i2v: "图生视频", music: "AI音乐" };
const PLACEHOLDERS = {
  t2i: "发出想象…　Enter 生成 4 图网格 · Shift+Enter 换行",
  i2i: "先放垫图（选择/拖入/粘贴），再描述想要的变化…",
  t2v: "描述动态画面（后端：FLUX 首帧 → Anima 图生视频）…",
  i2v: "先放首帧图片，再描述画面如何运动…",
  music: "描述音乐：风格、情绪、乐器、节奏…（ACE-Step 1.5：支持 4-10 分钟带人声完整乐曲）",
};
/* 视频后端参数白名单（app/tasks/video_worker.py _ALLOWED_PARAMS）。 */
const VIDEO_ALLOWED_PARAMS = new Set([
  "width", "height", "num_frames", "steps", "seed", "negative_prompt", "init_image_path",
]);
const NEEDS_REF = new Set(["i2i", "i2v"]);
const VIDEO_FPS = 24;           // anima.py export_to_video 固定 24fps
const snapFrames = (v) => { const n = clampInt(v, 5, 161); return n - ((n - 1) % 4); };
const isVideoUrl = (u) => /\.(mp4|webm|mov|mkv)$/i.test(u || "");
const isAudioUrl = (u) => /\.(wav|mp3|flac|ogg|m4a|aac)$/i.test(u || "");

const state = {
  sessions: [],
  activeId: null,
  settings: null,
  mode: "t2i",
  p: null,                    // = settings.modes[state.mode]：当前模式的参数桶
  engines: [],
  engineId: "flux-schnell",
  tasks: new Map(),          // task_id -> record
  ref: null,                 // {previewUrl, name, path}
  lightbox: null,            // {batchId, ids:[taskId], idx}
  lastSig: "",
  submitting: false,
  toastTimer: null,
};

/* ------------------------------------------------------------------ 存取 */

function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}
function saveJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 存储满时静默 */ }
}
const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
const randSeed = () => Math.floor(Math.random() * 0x7fffffff);
const clampInt = (v, lo, hi) => Math.max(lo, Math.min(hi, Math.round(v)));

function defaultModeParams(mode) {
  const p = { width: 1024, height: 1024, steps: 4, seedLock: false, seed: null, negative: "", advanced: "" };
  if (mode === "t2v" || mode === "i2v") { p.width = 512; p.height = 384; p.steps = 28; p.frames = 33; }
  if (mode === "i2i") p.strength = 0.6;
  return p;
}
function defaultSettings() {
  const modes = {};
  for (const m of MODES) modes[m] = defaultModeParams(m);
  return {
    mode: "t2i",
    refDir: "",
    engine: "flux-schnell",          // 图像模态引擎（t2i/i2i）
    vengine: "anima-image2video",    // 视频模态引擎（t2v/i2v）
    mengine: "ace-step-15",          // 音乐模态引擎（music，ACE-Step 1.5）
    bpm: 120, musicDuration: 180,    // 音乐参数（BPM 30-300；目标时长 10-480 秒）
    modes,
  };
}
function persist() {
  saveJSON(LS.sessions, state.sessions);
  saveJSON(LS.active, state.activeId);
  saveJSON(LS.settings, state.settings);
  saveJSON(LS.ui, {
    sideCollapsed: document.body.classList.contains("side-collapsed"),
    settingsHidden: document.body.classList.contains("settings-hidden"),
  });
}

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
  if (state.toastTimer) clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { els.toast.className = "toast hidden"; }, 3400);
}

async function api(path, options) {
  const response = await fetch(path, options);
  let payload = null;
  try { payload = await response.json(); } catch { /* 静态文件等非 JSON 响应 */ }
  if (!response.ok) {
    throw new Error((payload && payload.error) || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

function activeSession() {
  return state.sessions.find((s) => s.id === state.activeId) || null;
}

/* ------------------------------------------------------------------ 参数面板 */

function syncSizeInputs() {
  const s = state.p;
  $("width-range").value = Math.min(1408, Math.max(512, s.width));
  $("height-range").value = Math.min(1408, Math.max(512, s.height));
  $("width-num").value = s.width;
  $("height-num").value = s.height;
  for (const chip of $("aspect-chips").children) {
    chip.classList.toggle("on", Number(chip.dataset.w) === s.width && Number(chip.dataset.h) === s.height);
  }
  const px = s.width * s.height;
  const hint = $("vram-hint");
  if (px > PIXEL_SAFE) {
    hint.className = "hint warn";
    hint.textContent = `12GB 显存下 ${s.width}×${s.height}（${(px / 1e6).toFixed(2)}MP）有 OOM 风险，建议 ≤1024×1024。`;
  } else {
    hint.className = "hint";
    hint.textContent = `像素 ${(px / 1e6).toFixed(2)}MP · 12GB 显存安全范围内。`;
  }
  updateComposerMeta();
}

function syncVideoSize() {
  const s = state.p;
  $("vwidth-range").value = Math.min(1024, Math.max(256, s.width));
  $("vheight-range").value = Math.min(1024, Math.max(256, s.height));
  $("vwidth-num").value = s.width;
  $("vheight-num").value = s.height;
  for (const chip of $("aspect-chips-v").children) {
    chip.classList.toggle("on", Number(chip.dataset.w) === s.width && Number(chip.dataset.h) === s.height);
  }
  const px = s.width * s.height;
  const f = snapFrames(s.frames ?? 33);
  const hint = $("v-vram-hint");
  if (px > 480_000) {
    hint.className = "hint warn";
    hint.textContent = `视频 ${s.width}×${s.height} × ${f} 帧在 12GB 上偏紧（CPU offload 也吃力），建议 ≤512×384。`;
  } else {
    hint.className = "hint";
    hint.textContent = `视频像素 ${(px / 1e6).toFixed(2)}MP · Anima 走 bf16 + CPU offload，12GB 可行。`;
  }
  updateComposerMeta();
}

function syncStrength() {
  const v = Number(state.p.strength ?? 0.6);
  $("strength-range").value = v;
  $("strength-label").textContent = v.toFixed(2);
}

function syncFrames() {
  const f = snapFrames(state.p.frames ?? 33);
  state.p.frames = f;
  $("frames-range").value = f;
  $("frames-label").textContent = `${(f / VIDEO_FPS).toFixed(2)} 秒 · ${f} 帧 @24fps`;
  updateComposerMeta();
}

function syncMusic() {
  $("bpm-range").value = state.settings.bpm;
  $("bpm-label").textContent = `${state.settings.bpm} BPM`;
  $("music-dur-range").value = state.settings.musicDuration;
  $("music-dur-label").textContent = `${state.settings.musicDuration} 秒`;
}

function syncSteps() {
  const modality = MODALITY[state.mode];
  const range = $("steps-range");
  range.max = modality === "video" ? "50" : "12";
  range.value = Math.min(Number(range.max), Math.max(1, state.p.steps));
  $("steps-label").textContent = `${state.p.steps} 步`;
  $("steps-hint").textContent = modality === "video"
    ? "Anima 推荐 28 步（默认）；12GB 上步数直接决定等待时长。"
    : "schnell 推荐 4 步；更多步数收益低且更慢。";
  updateComposerMeta();
}

function syncSeed() {
  $("seed-lock").checked = state.p.seedLock;
  $("seed-num").value = state.p.seed ?? "";
  $("seed-num").disabled = !state.p.seedLock;
  $("seed-hint").textContent = MODALITY[state.mode] === "video"
    ? "不固定 = 每次随机 seed；固定 = 可复现同一段视频。"
    : "不固定时，一次想象 = 4 个不同 seed；固定时用连续 4 个 seed。";
  updateComposerMeta();
}

/* 切换模式后把该模式参数桶灌回共享控件。 */
function syncAll() {
  const modality = MODALITY[state.mode];
  if (modality === "music") syncMusic();
  else {
    if (modality === "image") syncSizeInputs(); else syncVideoSize();
    syncSteps();
    syncSeed();
    if (state.mode === "i2i") syncStrength();
    if (modality === "video") syncFrames();
    $("negative-input").value = state.p.negative;
    $("advanced-json").value = state.p.advanced || "";
    $("advanced-hint").textContent = modality === "video"
      ? "白名单：width height num_frames steps seed negative_prompt init_image_path"
      : "白名单：width height steps seed negative_prompt init_image_path strength";
  }
  syncMusic();
  updateComposerMeta();
}

/* ------------------------------------------------------------------ 模式 Tab 路由 */

function commitPanelText() {
  if (!state.p) return;
  state.p.negative = $("negative-input").value;
  state.p.advanced = $("advanced-json").value;
}

const IMAGINE_LABELS = { t2i: "想象", i2i: "想象（图生图）", t2v: "生成视频", i2v: "生成视频", music: "生成音乐" };

function hydrateChrome() {
  const mode = state.mode;
  const modality = MODALITY[mode];
  /* 参数面板：按 data-modes 显隐分区 */
  for (const sec of document.querySelectorAll("section[data-modes]")) {
    const modes = (sec.dataset.modes || "").split(/\s+/);
    sec.classList.toggle("hidden", !modes.includes(mode));
  }
  $("style-chips").classList.toggle("hidden", modality === "music");
  $("genre-chips").classList.toggle("hidden", modality !== "music");
  /* 垫图区：仅图生图 / 图生视频 */
  els.refZone.classList.toggle("hidden", !NEEDS_REF.has(mode));
  els.refZoneText.textContent = mode === "i2i"
    ? "垫图（必需）：点“选择图片”、拖入或粘贴图片"
    : "首帧图片（必需）：选择/拖入/粘贴首帧，再描述运动";
  els.prompt.placeholder = PLACEHOLDERS[mode];
  els.imagineBtn.textContent = IMAGINE_LABELS[mode] || "想象";
  renderTabs();
}

function setMode(mode) {
  if (!MODES.includes(mode) || mode === state.mode) return;
  commitPanelText();
  state.mode = mode;
  state.settings.mode = mode;
  if (!state.settings.modes[mode]) state.settings.modes[mode] = defaultModeParams(mode);
  state.p = state.settings.modes[mode];
  clearRef();
  hydrateChrome();
  syncAll();
  persist();
  refreshEngines();
  els.prompt.focus();
}

function parseAdvanced() {
  const text = (state.p.advanced || "").trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("必须是 JSON 对象");
    const clean = {};
    const rejected = [];
    const allowed = MODALITY[state.mode] === "video" ? VIDEO_ALLOWED_PARAMS : ALLOWED_PARAMS;
    for (const [key, value] of Object.entries(parsed)) {
      if (allowed.has(key)) clean[key] = value; else rejected.push(key);
    }
    if (rejected.length) showToast(`高级参数被忽略（不在白名单）：${rejected.join(", ")}`, "error");
    return clean;
  } catch (error) {
    showToast(`高级参数 JSON 无效：${error.message}`, "error");
    return {};
  }
}

function collectBaseParams() {
  const s = state.p;
  const base = { width: s.width, height: s.height, steps: s.steps };
  if (s.negative.trim()) base.negative_prompt = s.negative.trim();
  if (s.seedLock && Number.isInteger(s.seed)) base.seed = s.seed;
  const adv = parseAdvanced();
  Object.assign(base, adv); // 高级参数可覆盖（含 strength / init_image_path）
  return base;
}

function collectVideoParams(refPath) {
  const s = state.p;
  const params = { width: s.width, height: s.height, steps: s.steps, num_frames: snapFrames(s.frames ?? 33) };
  if (s.negative.trim()) params.negative_prompt = s.negative.trim();
  params.seed = (s.seedLock && Number.isInteger(s.seed)) ? s.seed : randSeed();
  Object.assign(params, parseAdvanced());
  if (refPath) params.init_image_path = refPath; // 垫图优先于高级参数里的路径
  return params;
}

function updateComposerMeta() {
  const modality = MODALITY[state.mode];
  let bits;
  if (modality === "music") {
    bits = [state.engineId || "ACE-Step 1.5（未注册）", `BPM ${state.settings.bpm}`, `目标 ${state.settings.musicDuration}s（上限 480s）`];
  } else if (modality === "video") {
    const f = snapFrames(state.p.frames ?? 33);
    bits = [`视频 ${state.p.width}×${state.p.height}`, `${(f / VIDEO_FPS).toFixed(2)}s · ${f} 帧`,
      `steps ${state.p.steps}`, state.p.seedLock ? `seed ${state.p.seed}` : "seed 随机"];
  } else {
    bits = [`${state.p.width}×${state.p.height}`, `steps ${state.p.steps}`,
      state.p.seedLock ? `seed ${state.p.seed}` : "seed 随机"];
    if (state.mode === "i2i") bits.push(`strength ${Number(state.p.strength ?? 0.6).toFixed(2)}`);
  }
  if (NEEDS_REF.has(state.mode)) bits.push(state.ref ? (state.ref.path ? "垫图：已挂" : "垫图：待确认路径") : "垫图：未挂");
  els.composerMeta.textContent = bits.join(" · ");
  syncStyleChips();
}

/* 风格/曲风标签：勾选态 = 提示词里是否包含该文本（直接写在提示词里）。 */
function syncStyleChips() {
  const text = els.prompt.value;
  for (const container of [$("style-chips"), $("genre-chips")]) {
    for (const chip of container.children) chip.classList.toggle("on", text.includes(chip.dataset.suffix));
  }
}

/* ------------------------------------------------------------------ 引擎与健康的轮询 */

async function refreshHealth() {
  try {
    await api("/api/health");
    els.healthPill.textContent = "服务正常";
    els.healthPill.className = "pill ok";
  } catch {
    els.healthPill.textContent = "无法连接本地服务";
    els.healthPill.className = "pill bad";
  }
}

async function refreshEngines() {
  let payload;
  try { payload = await api("/api/engines"); } catch { return; }
  state.engines = payload.engines || [];
  const modality = MODALITY[state.mode];
  els.engineRestart.disabled = false;
  const options = state.engines.filter((e) => {
    const types = ENGINE_TASK_TYPES[e.engine_id];
    return !types || types.includes(modality);
  });
  els.engineSelect.textContent = "";
  if (!options.length) {
    els.engineSelect.appendChild(el("option", null,
      modality === "video" ? "未注册视频引擎"
        : modality === "music" ? "未注册音乐引擎（检查 .venv-music 与权重）"
          : "未注册图像引擎（检查 engines_enabled）"));
    els.enginePill.textContent = "无引擎"; els.enginePill.className = "pill bad";
    els.engineState.textContent = modality === "music"
      ? "音乐引擎未注册：需 .venv-music 环境与 vendor\\ACE-Step-1.5（运行 scripts\\setup_music_venv.ps1），未就绪前不会提交、不伪造输出"
      : "引擎未注册";
    state.engineId = "";
    return;
  }
  const key = modality === "video" ? "vengine" : modality === "music" ? "mengine" : "engine";
  if (!options.some((e) => e.engine_id === state.settings[key])) state.settings[key] = options[0].engine_id;
  state.engineId = state.settings[key];
  for (const engine of options) {
    const opt = el("option", null, engine.engine_id);
    opt.value = engine.engine_id;
    opt.selected = engine.engine_id === state.engineId;
    els.engineSelect.appendChild(opt);
  }
  const current = state.engines.find((e) => e.engine_id === state.engineId);
  const label = current ? (ENGINE_STATE_LABELS[current.state] || current.state) : "?";
  els.enginePill.textContent = `${state.engineId} · ${label}`;
  els.enginePill.className = "pill " + (current && current.state === "ready" ? "ok" : current && current.state === "failed" ? "bad" : "");
  const hint = $("engine-mode-hint");
  if (hint) hint.textContent = modality === "video"
    ? "Anima 权重未预下载：首次生成会联网下载（十几分钟量级）。出视频前先释放本机 LLM 引擎显存（scripts\\release_local_engine.bat）。"
    : modality === "music"
      ? "ACE-Step 1.5（MIT 权重，本地离线）：48kHz wav，最低 12GB 显存，与图片/视频共用 FIFO 串行队列不并发抢占。出音乐前先释放本机 LLM 引擎显存（scripts\\release_local_engine.bat）。"
      : "出图前先释放本机 LLM 引擎显存（scripts\\release_local_engine.bat），否则易 OOM。";
  els.engineState.textContent = current ? (current.message || label) : label;
}

/* ------------------------------------------------------------------ 任务轮询与渲染 */

async function refreshTasks() {
  let payload;
  try { payload = await api("/api/tasks?limit=300"); } catch { return; }
  state.tasks = new Map(payload.tasks.map((t) => [t.task_id, t]));
  // 会话缩略图：取任意完成任务里第一张“图片”产物（视频任务先用首帧 png，纯音频留占位）
  let dirty = false;
  for (const session of state.sessions) {
    if (session.thumb) continue;
    outer: for (const batch of session.batches) {
      for (const id of batch.taskIds) {
        const rec = state.tasks.get(id);
        const file = rec && rec.status === "completed"
          && (rec.output_files || []).find((f) => f.url && !isVideoUrl(f.url) && !isAudioUrl(f.url));
        if (file) { session.thumb = file.url; dirty = true; break outer; }
      }
    }
  }
  if (dirty) persist();
  renderSessions();
  renderFeed();
  renderTabs();
}

function activeCount(taskType) {
  let n = 0;
  for (const r of state.tasks.values()) {
    if (r.task_type === taskType && (r.status === "queued" || r.status === "preparing" || r.status === "running")) n++;
  }
  return n;
}

function renderTabs() {
  for (const tab of els.modeTabs.children) {
    tab.classList.toggle("on", tab.dataset.mode === state.mode);
    const badge = tab.querySelector(".tab-badge");
    if (!badge) continue;
    const modality = MODALITY[tab.dataset.mode];
    const n = activeCount(modality);
    badge.textContent = n ? String(n) : "";
  }
}

function queuePosition(rec) {
  let pos = 1;
  for (const other of state.tasks.values()) {
    if (other.task_id !== rec.task_id && other.status === "queued" && String(other.created_at) < String(rec.created_at)) pos++;
  }
  return pos;
}

function batchSig(batch) {
  return batch.taskIds.map((id) => {
    const r = state.tasks.get(id);
    return r ? `${id}:${r.status}:${Math.round((r.progress ?? -1) * 100)}:${r.output_files.length}` : `${id}:?`;
  }).join(",");
}

function feedSig() {
  const session = activeSession();
  if (!session) return `none:${state.sessions.length}`;
  return session.id + "|" + session.batches.map(batchSig).join(";");
}

function renderFeed(force) {
  if (!force && feedSig() === state.lastSig) return;
  const nearBottom = els.feed.scrollHeight - els.feed.scrollTop - els.feed.clientHeight < 240;
  const session = activeSession();
  els.feed.textContent = "";
  els.sessionTitle.textContent = session ? session.title : "Imagine";
  if (!session || !session.batches.length) {
    els.feed.appendChild(els.feedEmpty);
    els.feedEmpty.classList.remove("hidden");
    state.lastSig = feedSig();
    return;
  }
  els.feedEmpty.classList.add("hidden");
  for (const batch of session.batches) els.feed.appendChild(batchNode(batch));
  state.lastSig = feedSig();
  if (nearBottom) els.feed.scrollTop = els.feed.scrollHeight;
}

function batchNode(batch) {
  const wrap = el("div", "batch");
  const userRow = el("div", "user-row");
  const bubble = el("div", "user-bubble");
  const kindLabel = { imagine: "想象", upscale: "U 放大", variation: "V 变化", video: "视频", music: "音乐" }[batch.kind] || batch.kind;
  bubble.appendChild(el("div", "p", batch.prompt));
  const meta = el("div", "meta");
  meta.appendChild(el("span", "tag", kindLabel));
  if (batch.mode) meta.appendChild(el("span", "tag", MODE_LABELS[batch.mode] || batch.mode));
  if (batch.time) meta.appendChild(el("span", "tag", batch.time));
  if (batch.refPath) meta.appendChild(el("span", "tag ref", `参考图 ${batch.refPath.split(/[\\/]/).pop()}`));
  bubble.appendChild(meta);
  userRow.appendChild(bubble);
  wrap.appendChild(userRow);

  const grid = el("div", "grid" + (batch.taskIds.length === 1 ? " grid-one" : ""));
  for (const id of batch.taskIds) grid.appendChild(tileNode(id, batch));
  wrap.appendChild(grid);
  return wrap;
}

function tileSizeRatio(rec, batch) {
  const p = (rec && rec.parameters) || batch.params || state.settings;
  const w = Number(p.width) || 1024, h = Number(p.height) || 1024;
  return `${(h / w) * 100}%`;
}

function renderVideoTile(tile, rec, files) {
  for (const f of files) {
    if (isVideoUrl(f.url)) {
      const v = el("video");
      v.controls = true; v.muted = true; v.loop = true; v.preload = "metadata";
      v.src = f.url;
      tile.appendChild(v);
    } else if (/\.(png|jpe?g|webp)$/i.test(f.url)) {
      const img = el("img", "frame");
      img.loading = "lazy"; img.src = f.url; img.alt = "首帧";
      tile.appendChild(img);
    }
  }
  const p = rec.parameters || {};
  const frames = Number(p.num_frames) || 0;
  tile.appendChild(el("div", "tile-foot",
    `seed ${p.seed ?? "—"} · ${p.width ?? "?"}×${p.height ?? "?"} · ${frames ? `${(frames / VIDEO_FPS).toFixed(2)}s @24fps` : "时长?"}`));
  const vid = files.find((f) => isVideoUrl(f.url));
  if (vid) {
    const a = el("a", "vu dl", "⬇下载视频");
    a.href = vid.url; a.download = vid.name || "辰鹄视频.mp4";
    const acts = el("div", "tile-acts");
    acts.appendChild(a);
    tile.appendChild(acts);
  }
}

function renderAudioTile(tile, rec, files) {
  const item = files.find((f) => isAudioUrl(f.url));
  if (!item) { tile.appendChild(el("div", "msg muted", "无音频产物")); return; }
  const au = el("audio");
  au.controls = true; au.preload = "metadata";
  au.src = item.url;
  tile.appendChild(au);
  const p = rec.parameters || {};
  const targetDur = Number(p.duration) || 0;
  tile.appendChild(el("div", "tile-foot",
    `BPM ${p.bpm ?? "—"} · 目标 ${targetDur ? `${Math.round(targetDur)}s` : "?"} · seed ${p.seed ?? "—"}`));
  const dl = el("a", "vu dl", "⬇下载音频");
  dl.href = item.url; dl.download = item.name || "辰鹄音乐.wav";
  const acts = el("div", "tile-acts");
  acts.appendChild(dl);
  tile.appendChild(acts);
}

function tileNode(taskId, batch) {
  const rec = state.tasks.get(taskId);
  if (!rec) return el("div", "tile", "任务未找到（可能被后端清理）");
  const tile = el("div", "tile");
  tile.dataset.task = taskId;
  tile.dataset.batch = batch.id;

  if (rec.status === "completed") {
    const files = (rec.output_files || []).filter((f) => f.url);
    if (rec.task_type === "video") { renderVideoTile(tile, rec, files); return tile; }
    if (rec.task_type === "music") { renderAudioTile(tile, rec, files); return tile; }
    const file = files[0];
    if (!file) {
      const first = (rec.output_files || [])[0];
      if (first) tile.appendChild(el("div", "msg muted", `产物不在输出目录：${first.path}`));
      return tile;
    }
    const img = el("img");
    img.loading = "lazy";
    img.src = file.url;
    img.alt = rec.prompt;
    img.dataset.task = taskId;
    tile.appendChild(img);
    tile.appendChild(el("div", "tile-foot", `seed ${rec.parameters?.seed ?? "—"} · ${rec.parameters?.width ?? "?"}×${rec.parameters?.height ?? "?"}`));
    const acts = el("div", "tile-acts");
    const btnV = el("button", "vu"); btnV.innerHTML = "<b>V</b>变化";
    btnV.dataset.act = "variation"; btnV.dataset.task = taskId;
    const btnU = el("button", "vu"); btnU.innerHTML = "<b>U</b>放大";
    btnU.dataset.act = "upscale"; btnU.dataset.task = taskId;
    acts.append(btnV, btnU);
    tile.appendChild(acts);
    return tile;
  }

  if (rec.status === "failed" || rec.status === "cancelled") {
    tile.classList.add("err");
    const retried = [...state.tasks.values()].some((t) => t.retry_of === taskId);
    if (rec.status === "failed") {
      tile.appendChild(el("div", "msg", rec.error_message || "任务失败"));
    } else {
      tile.appendChild(el("div", "msg muted", "已取消"));
    }
    const row = el("div", "row");
    if (!retried) {
      const btn = el("button", "btn-mini", "重试");
      btn.dataset.act = "retry"; btn.dataset.task = taskId;
      row.appendChild(btn);
    } else {
      row.appendChild(el("span", "muted", "已重试 →"));
    }
    tile.appendChild(row);
    return tile;
  }

  /* queued / preparing / running：骨架 + 进度 */
  const skel = el("div", "skel");
  skel.style.paddingTop = rec.task_type === "music" ? "38%" : tileSizeRatio(rec, batch);
  tile.appendChild(skel);
  if (rec.status === "queued" || rec.status === "preparing" || rec.status === "running") {
    const cancel = el("button", "btn-cancel-tile", "取消");
    cancel.dataset.act = "cancel"; cancel.dataset.task = taskId;
    tile.appendChild(cancel);
  }
  const label = el("div", "skel-label");
  if (rec.task_type === "video") label.appendChild(el("span", "muted", "视频管线：首帧 → 去噪 → 解码 → 编码，渲染需数分钟"));
  if (rec.task_type === "music") label.appendChild(el("span", "muted", "音乐管线：加载权重（首次约 1-2 分钟）→ LM 谱曲填词 → DiT 去噪 → VAE 解码渲染 48kHz wav"));
  const bar = el("div", "bar");
  const fill = el("i");
  if (rec.status === "queued") {
    label.appendChild(el("span", null, `排队中 · 第 ${queuePosition(rec)} 位`));
  } else {
    const pct = typeof rec.progress === "number"
      ? clampInt(rec.progress <= 1 ? rec.progress * 100 : rec.progress, 0, 100)
      : null;
    label.appendChild(el("span", null, rec.status === "preparing" ? "加载模型…" : `生成中 ${pct ?? ""}${pct !== null ? "%" : ""}`));
    if (pct === null) bar.classList.add("indet");
    else fill.style.width = pct + "%";
  }
  bar.appendChild(fill);
  label.appendChild(bar);
  tile.appendChild(label);
  return tile;
}

/* ------------------------------------------------------------------ 提交：想象 / V / U */

async function submitTask(prompt, parameters, taskType = "image", engineId = null) {
  return api("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task_type: taskType,
      engine_id: engineId || state.settings[taskType === "video" ? "vengine" : "engine"],
      prompt,
      model_id: null,
      parameters,
    }),
  });
}

function ensureSession(title) {
  let session = activeSession();
  if (session) return session;
  session = { id: uid(), title: (title || "新会话").slice(0, 48), createdAt: Date.now(), thumb: null, batches: [] };
  state.sessions.unshift(session);
  state.activeId = session.id;
  return session;
}

async function imagine() {
  if (state.submitting) return;
  const text = els.prompt.value.trim();
  if (!text) { showToast("先写提示词", "error"); return; }
  if (!state.engines.length) { showToast("没有已注册的引擎，请先检查 engines_enabled 与引擎环境", "error"); return; }
  const modality = MODALITY[state.mode];
  if (modality === "music") {
    await imagineMusic(text);
    return; // imagineMusic 自行处理显存检查、边界确认、提交与提示
  }
  if (!NEEDS_REF.has(state.mode) && state.ref) clearRef(); // 文生图/文生视频不挂垫图
  let extraParams = {};
  if (state.ref) {
    const path = state.ref.path.trim();
    if (!path) { showToast("参考图需要本机完整路径（浏览器拿不到剪贴板图片的磁盘路径）。填路径后再提交，或点 ✕ 取消引用。", "error"); return; }
    if (!/^[A-Za-z]:[\\/]/.test(path)) { showToast("参考图路径必须是盘符开头的绝对路径，例如 D:\\ChenguAI\\uploads\\photo.png", "error"); return; }
    extraParams.init_image_path = path;
    const cut = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
    if (cut > 1) { state.settings.refDir = path.slice(0, cut); persist(); } // 记住目录，下次预填
  }

  const refPath = extraParams.init_image_path || "";
  if (NEEDS_REF.has(state.mode) && !refPath) {
    showToast(state.mode === "i2i"
      ? "图生图需要先放垫图：点“选择图片”、拖入或粘贴，确认本机路径后再提交。"
      : "图生视频需要先放首帧图片（也可用图片瓦片下的入口把已生成图推成视频）。", "error");
    return;
  }
  state.submitting = true;
  els.imagineBtn.disabled = true;
  try {
    const session = ensureSession(text);
    if (modality === "video") {
      const vparams = collectVideoParams(refPath);
      const vbatch = { id: uid(), kind: "video", mode: state.mode, prompt: text, time: formatTime(new Date().toISOString()), refPath, params: vparams, taskIds: [] };
      session.batches.push(vbatch);
      try {
        const rec = await submitTask(text, vparams, "video", state.settings.vengine);
        vbatch.taskIds.push(rec.task_id);
        showToast(refPath
          ? "视频任务已排队：产出首帧 + mp4，渲染通常需数分钟；首次运行会自动下载 Anima 权重（十几分钟量级）"
          : "文生视频已排队：后端先由 FLUX 出首帧 → Anima 渲染视频；首次运行会自动下载 Anima 权重", "success");
      } catch (error) { showToast(`视频提交失败：${error.message}`, "error"); }
      els.prompt.value = "";
      autosize();
      clearRef();
      persist();
      renderSessions();
      renderFeed(true);
      els.feed.scrollTop = els.feed.scrollHeight;
      return; // finally 会恢复按钮
    }
    const base = collectBaseParams();
    if (state.mode === "i2i" && !("strength" in base)) base.strength = Number(state.p.strength ?? 0.6);
    Object.assign(base, extraParams);
    const fullPrompt = text; // 风格标签已直接内联进提示词，不再另行追加
    const batch = {
      id: uid(), kind: "imagine", mode: state.mode, prompt: text,
      time: formatTime(new Date().toISOString()),
      refPath, params: base, taskIds: [],
    };
    session.batches.push(batch);
    for (let i = 0; i < 4; i++) {
      const params = { ...base };
      if (state.p.seedLock && Number.isInteger(state.p.seed)) params.seed = state.p.seed + i;
      else params.seed = randSeed();
      try {
        const rec = await submitTask(fullPrompt, params, "image", state.settings.engine);
        batch.taskIds.push(rec.task_id);
      } catch (error) {
        showToast(`第 ${i + 1} 张提交失败：${error.message}`, "error");
      }
      renderFeed(true);
    }
    els.prompt.value = "";
    autosize();
    clearRef();
    persist();
    renderSessions();
    renderFeed(true);
    els.feed.scrollTop = els.feed.scrollHeight;
  } finally {
    state.submitting = false;
    els.imagineBtn.disabled = false;
  }
}

/* 提交音乐（ACE-Step 1.5）：先做显存门槛前置检查——不足则前端直接拦截，
 * 不向后端抛任务；再弹窗确认能力边界（支持 4-10 分钟带人声完整乐曲，
 * 不支持超长多轨道复杂编曲；12GB 档实际最长约 8 分钟，超出会被引擎截短）。
 * 队列与图片/视频共用 FIFO 串行，不并发抢占显存。 */
async function imagineMusic(text) {
  if (!state.engines.some((e) => (ENGINE_TASK_TYPES[e.engine_id] || []).includes("music"))) {
    showToast("音乐引擎未注册：需 .venv-music 与 vendor\\ACE-Step-1.5（运行 scripts\\setup_music_venv.ps1）及权重下载；引擎未就绪前不会提交，也不会伪造输出。", "error");
    return;
  }
  let hw = null;
  try { hw = await api("/api/hardware"); } catch { /* 探测失败不误判，交由后端 health_check 兜底 */ }
  if (hw && !hw.gpu) {
    showToast("显存不足，无法启动ACE-Step1.5音乐模型：检测不到 NVIDIA 显卡（最低要求 12GB 显存）。", "error");
    return;
  }
  if (hw && hw.gpu && hw.gpu.memory_total_mb < MUSIC_MIN_VRAM_MB) {
    showToast(`显存不足，无法启动ACE-Step1.5音乐模型：显卡 ${hw.gpu.name || "?"} 仅 ${hw.gpu.memory_total_mb} MB，最低要求 ${MUSIC_MIN_VRAM_MB} MB（12GB）。`, "error");
    return;
  }
  if (hw && hw.gpu && hw.gpu.memory_free_mb < 7000) {
    showToast(`显存不足，无法启动ACE-Step1.5音乐模型：当前空闲显存仅 ${hw.gpu.memory_free_mb} MB，GPU 被其他程序占满。请先释放显存（双击 scripts\\release_local_engine.bat）再提交。`, "error");
    return;
  }
  const ok = window.confirm(
    "ACE-Step 1.5 能力边界确认：\n"
    + "· 支持 4-10 分钟带人声完整乐曲（人声+器乐）或纯器乐，输出 48kHz wav；\n"
    + "· 本机 12GB 显存档位实际最长约 8 分钟，更长的目标时长会被引擎自动截到上限（产物按真实时长为准）；\n"
    + "· 不支持超长多轨道复杂编曲（无分轨导出/多轨工程）。\n\n"
    + `本次任务：BPM ${state.settings.bpm} · 目标 ${state.settings.musicDuration}s · 与图片/视频共用串行队列（不并发抢占显存）。\n继续提交？`
  );
  if (!ok) return;
  state.submitting = true;
  els.imagineBtn.disabled = true;
  try {
    const session = ensureSession(text);
    const params = {
      bpm: state.settings.bpm,
      duration: state.settings.musicDuration,
      vocal_language: "zh",
      instrumental: false, // 带人声，歌词由 LM 依风格描述自动创作；纯器乐可在提示词里注明
      seed: randSeed(),
    };
    const batch = { id: uid(), kind: "music", mode: state.mode, prompt: text, time: formatTime(new Date().toISOString()), refPath: "", params, taskIds: [] };
    session.batches.push(batch);
    try {
      const rec = await submitTask(text, params, "music", state.settings.mengine);
      batch.taskIds.push(rec.task_id);
      showToast("音乐任务已排队（串行队列）：首次生成需加载约 10GB 本地权重（约 1-2 分钟），产出 48kHz wav。", "success");
    } catch (error) { showToast(`音乐提交失败：${error.message}`, "error"); }
    els.prompt.value = "";
    autosize();
    persist();
    renderSessions();
    renderFeed(true);
    els.feed.scrollTop = els.feed.scrollHeight;
  } finally {
    state.submitting = false;
    els.imagineBtn.disabled = false;
  }
}

function upscaleSourceRecord(taskId) {
  const rec = state.tasks.get(taskId);
  const file = rec && rec.output_files[0];
  if (!rec || rec.status !== "completed" || !file || !file.path) {
    showToast("该任务没有可用的本地产物文件", "error");
    return null;
  }
  return { rec, file };
}

async function upscale(taskId) {
  const src = upscaleSourceRecord(taskId);
  if (!src) return;
  const p = src.rec.parameters || {};
  const w0 = Number(p.width) || 1024, h0 = Number(p.height) || 1024;
  let w = clampInt(w0 * 1.5 / 8, 64, 176) * 8, h = clampInt(h0 * 1.5 / 8, 64, 176) * 8;
  if (w * h > PIXEL_SAFE) { const f = Math.sqrt(PIXEL_SAFE / (w * h)); w = clampInt(w * f / 8, 32, 176) * 8; h = clampInt(h * f / 8, 32, 176) * 8; }
  const params = {
    width: w, height: h, steps: Number(p.steps) || 4,
    init_image_path: src.file.path, strength: 0.45,
    seed: Number.isInteger(p.seed) ? p.seed : randSeed(),
  };
  if (p.negative_prompt) params.negative_prompt = p.negative_prompt;
  const session = ensureSession(src.rec.prompt);
  const batch = { id: uid(), kind: "upscale", mode: "i2i", prompt: src.rec.prompt, time: formatTime(new Date().toISOString()), params, taskIds: [] };
  session.batches.push(batch);
  try {
    const rec = await submitTask(src.rec.prompt, params, "image", state.settings.engine);
    batch.taskIds.push(rec.task_id);
    persist(); renderFeed(true);
    showToast(`放大任务已排队：${w}×${h}（img2img 低强度重建细节）`, "success");
  } catch (error) { showToast(`放大提交失败：${error.message}`, "error"); }
}

async function variation(taskId) {
  const src = upscaleSourceRecord(taskId); // 与 U 相同的取源逻辑
  if (!src) return;
  const p = src.rec.parameters || {};
  const session = ensureSession(src.rec.prompt);
  const batch = { id: uid(), kind: "variation", mode: "i2i", prompt: src.rec.prompt, time: formatTime(new Date().toISOString()), params: p, taskIds: [] };
  session.batches.push(batch);
  const lockSeed = state.p.seedLock && Number.isInteger(state.p.seed);
  try {
    for (let i = 0; i < 4; i++) {
      const params = {
        width: Number(p.width) || 1024, height: Number(p.height) || 1024,
        steps: Number(p.steps) || 4,
        init_image_path: src.file.path, strength: 0.7,
        seed: lockSeed ? state.p.seed + i : randSeed(),
      };
      if (p.negative_prompt) params.negative_prompt = p.negative_prompt;
      const rec = await submitTask(src.rec.prompt, params, "image", state.settings.engine);
      batch.taskIds.push(rec.task_id);
      renderFeed(true);
    }
    persist();
    showToast("变体任务已排队（4 张）", "success");
  } catch (error) { showToast(`变体提交失败：${error.message}`, "error"); }
}

async function retryTask(taskId) {
  const rec = state.tasks.get(taskId);
  try {
    const newRec = await api(`/api/tasks/${taskId}/retry`, { method: "POST" });
    for (const session of state.sessions) {
      for (const batch of session.batches) {
        if (batch.taskIds.includes(taskId)) { batch.taskIds.push(newRec.task_id); persist(); renderFeed(true); return; }
      }
    }
    // 任务不在任何会话里：挂到当前会话（模式按任务类型推导）
    const session = ensureSession(rec ? rec.prompt : "重试");
    const srcRec = rec || newRec;
    const fbMode = srcRec.task_type === "video"
      ? (srcRec.parameters && srcRec.parameters.init_image_path ? "i2v" : "t2v")
      : (srcRec.parameters && srcRec.parameters.init_image_path ? "i2i" : "t2i");
    session.batches.push({ id: uid(), kind: srcRec.task_type === "video" ? "video" : "imagine", mode: fbMode, prompt: srcRec.prompt || "重试", time: formatTime(new Date().toISOString()), params: srcRec.parameters || {}, taskIds: [newRec.task_id] });
    persist(); renderFeed(true);
  } catch (error) { showToast(`重试失败：${error.message}`, "error"); }
}

async function cancelTask(taskId) {
  try {
    await api(`/api/tasks/${taskId}/cancel`, { method: "POST" });
    showToast("已请求取消", "success");
  } catch (error) { showToast(`取消失败：${error.message}`, "error"); }
}

/* ------------------------------------------------------------------ 会话侧栏 */

function renderSessions() {
  els.sessionList.textContent = "";
  for (const session of state.sessions) {
    const item = el("div", "side-item" + (session.id === state.activeId ? " active" : ""));
    let thumb;
    if (session.thumb) {
      thumb = el("img", "side-thumb");
      thumb.src = session.thumb;
      thumb.alt = "";
      thumb.loading = "lazy";
    } else {
      thumb = el("div", "side-thumb");
    }
    item.append(thumb, el("div", "t", session.title));
    item.querySelector(".t").addEventListener("dblclick", (event) => {
      event.stopPropagation();
      startRename(session, item);
    });
    const rename = el("button", "side-del rename", "✎");
    rename.title = "重命名会话";
    rename.addEventListener("click", (event) => {
      event.stopPropagation();
      startRename(session, item);
    });
    item.appendChild(rename);
    const del = el("button", "side-del", "🗑");
    del.title = "删除会话（只删本地记录，服务器任务保留）";
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!confirm(`删除会话「${session.title}」？\n（仅删除本浏览器的会话分组，服务器上的任务与产物不受影响）`)) return;
      state.sessions = state.sessions.filter((s) => s.id !== session.id);
      if (state.activeId === session.id) state.activeId = state.sessions[0]?.id || null;
      persist(); renderSessions(); renderFeed(true);
    });
    item.appendChild(del);
    item.addEventListener("click", () => {
      state.activeId = session.id;
      persist(); renderSessions(); renderFeed(true);
    });
    els.sessionList.appendChild(item);
  }
}

/* 会话重命名：双击标题或点 ✎ 后原位变成输入框；Enter 保存，Esc 取消。 */
function startRename(session, item) {
  const label = item.querySelector(".t");
  if (!label) return;
  const input = el("input", "side-rename");
  input.value = session.title;
  item.replaceChild(input, label);
  input.focus();
  input.select();
  let done = false;
  const commit = (save) => {
    if (done) return;
    done = true;
    if (save) session.title = input.value.trim() || session.title;
    persist();
    renderSessions();
    const active = activeSession();
    if (active && active.id === session.id) els.sessionTitle.textContent = session.title;
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") commit(true);
    else if (event.key === "Escape") commit(false);
  });
  input.addEventListener("blur", () => commit(true));
  input.addEventListener("click", (event) => event.stopPropagation());
}

function newSession() {
  const current = activeSession();
  if (current && !current.batches.length) { els.prompt.focus(); return; } // 当前已是空会话：不重复创建
  const session = { id: uid(), title: "新会话", createdAt: Date.now(), thumb: null, batches: [] };
  state.sessions.unshift(session);
  state.activeId = session.id;
  persist(); renderSessions(); renderFeed(true);
  els.prompt.focus();
}

/* ------------------------------------------------------------------ 参考图（图生图） */

function setRef(file) {
  if (state.ref) URL.revokeObjectURL(state.ref.previewUrl);
  const name = (file.name || "clipboard-image.png").replace(/[<>:"|?*]/g, "_");
  // 纯前端的尽力自动化：记住的目录 + 图片自身文件名 → 预填路径，用户通常只需核对文件名。
  const guess = state.settings.refDir ? `${state.settings.refDir}\\${name}` : "";
  state.ref = { previewUrl: URL.createObjectURL(file), name, path: guess };
  renderRefChip();
  showToast("参考图已载入预览。确认/修正路径即可提交（目录已自动记住；浏览器无法读取剪贴板图片的真实磁盘路径，V/U 从已生成图走图生图不受此限）。", "success");
}

function clearRef() {
  if (state.ref) URL.revokeObjectURL(state.ref.previewUrl);
  state.ref = null;
  renderRefChip();
}

function renderRefChip() {
  if (!state.ref) { els.refChip.classList.add("hidden"); els.refChip.textContent = ""; updateComposerMeta(); return; }
  els.refChip.classList.remove("hidden");
  els.refChip.textContent = "";
  const img = el("img"); img.src = state.ref.previewUrl; img.alt = "";
  const input = el("input", "path");
  input.placeholder = "参考图的本机完整路径（绝对路径），如 D:\\ChenguAI\\uploads\\photo.png";
  input.value = state.ref.path;
  input.addEventListener("input", () => {
    state.ref.path = input.value.trim();
    const m = state.ref.path.match(/^(.*[\\/]).+$/);
    if (m && /^[A-Za-z]:[\\/]/.test(state.ref.path)) {
      state.settings.refDir = m[1].replace(/[\\/]+$/, "");
      persist();
    }
    updateComposerMeta();
  });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); els.prompt.focus(); } });
  const note = el("span", "note", MODALITY[state.mode] === "video"
    ? "该文件将作为 init_image_path 首帧，视频从它开始运动"
    : "强度用左侧「重绘强度」滑块调节（默认 0.6）");
  const close = el("button", "icon-btn", "✕");
  close.addEventListener("click", clearRef);
  els.refChip.append(img, el("span", "muted", state.ref.name), input, note, close);
  updateComposerMeta();
}

function handleImageFiles(files) {
  const file = [...(files || [])].find((f) => f.type && f.type.startsWith("image/"));
  if (!file) return false;
  setRef(file);
  return true;
}

/* ------------------------------------------------------------------ 放大查看（lightbox） */

function batchCompletedIds(batch) {
  return batch.taskIds.filter((id) => {
    const r = state.tasks.get(id);
    return r && r.status === "completed" && r.output_files[0] && r.output_files[0].url;
  });
}

function findBatch(batchId) {
  for (const session of state.sessions) {
    const batch = session.batches.find((b) => b.id === batchId);
    if (batch) return batch;
  }
  return null;
}

function openLightbox(batchId, taskId) {
  const batch = findBatch(batchId);
  if (!batch) return;
  const ids = batchCompletedIds(batch);
  const idx = Math.max(0, ids.indexOf(taskId));
  state.lightbox = { batchId, ids, idx };
  renderLightbox();
  $("lightbox").classList.remove("hidden");
}

function renderLightbox() {
  const lb = state.lightbox;
  if (!lb) return;
  const rec = state.tasks.get(lb.ids[lb.idx]);
  if (!rec || !rec.output_files[0]) return;
  const file = rec.output_files[0];
  $("lb-img").src = file.url;
  const save = $("lb-save");
  save.href = file.url;
  save.download = file.name || "辰鹄图片.png";
  const p = rec.parameters || {};
  $("lb-meta").textContent = `${rec.prompt}\n${p.width ?? "?"}×${p.height ?? "?"} · steps ${p.steps ?? "?"} · seed ${p.seed ?? "随机"}${p.init_image_path ? " · img2img" : ""} · ${formatTime(rec.finished_at)}`;
  $("lb-meta").style.whiteSpace = "pre-wrap";
  $("lb-prev").classList.toggle("hidden", lb.ids.length < 2);
  $("lb-next").classList.toggle("hidden", lb.ids.length < 2);
}

function closeLightbox() {
  state.lightbox = null;
  $("lightbox").classList.add("hidden");
}

function lbShift(delta) {
  const lb = state.lightbox;
  if (!lb || lb.ids.length < 2) return;
  lb.idx = (lb.idx + delta + lb.ids.length) % lb.ids.length;
  renderLightbox();
}

/* ------------------------------------------------------------------ 事件绑定 */

function autosize() {
  els.prompt.style.height = "auto";
  els.prompt.style.height = Math.min(180, els.prompt.scrollHeight) + "px";
}

function bindEvents() {
  /* Imagine 输入 */
  els.prompt.addEventListener("input", () => { autosize(); syncStyleChips(); });
  els.prompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      imagine();
    }
  });
  els.imagineBtn.addEventListener("click", imagine);
  els.prompt.addEventListener("paste", (event) => {
    if (handleImageFiles(event.clipboardData && event.clipboardData.files)) event.preventDefault();
  });
  els.composer.addEventListener("dragover", (event) => event.preventDefault());
  els.composer.addEventListener("drop", (event) => {
    if (handleImageFiles(event.dataTransfer && event.dataTransfer.files)) event.preventDefault();
  });

  /* 模式 Tab 与垫图选择按钮 */
  els.modeTabs.addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab && tab.dataset.mode) setMode(tab.dataset.mode);
  });
  els.pickRef.addEventListener("click", () => els.fileInput.click());
  els.fileInput.addEventListener("change", () => {
    handleImageFiles(els.fileInput.files);
    els.fileInput.value = "";
  });

  /* 侧栏折叠 */
  $("collapse-side").addEventListener("click", () => { document.body.classList.add("side-collapsed"); $("expand-side").classList.remove("hidden"); persist(); });
  $("expand-side").addEventListener("click", () => { document.body.classList.remove("side-collapsed"); $("expand-side").classList.add("hidden"); persist(); });
  $("new-session").addEventListener("click", newSession);

  /* 参数面板开合 */
  $("toggle-settings").addEventListener("click", () => { document.body.classList.toggle("settings-hidden"); persist(); });
  $("close-settings").addEventListener("click", () => { document.body.classList.add("settings-hidden"); persist(); });

  /* 尺寸（图片 / 视频分别绑到当前模式参数桶） */
  $("aspect-chips").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    state.p.width = Number(chip.dataset.w);
    state.p.height = Number(chip.dataset.h);
    syncSizeInputs(); persist();
  });
  const bindSize = (rangeId, numId, key, sync, lo, hi, step) => {
    $(rangeId).addEventListener("input", () => { state.p[key] = Number($(rangeId).value); sync(); persist(); });
    $(numId).addEventListener("change", () => {
      const v = clampInt(Number($(numId).value) || 512, lo, hi);
      state.p[key] = Math.round(v / step) * step;
      sync(); persist();
    });
  };
  bindSize("width-range", "width-num", "width", syncSizeInputs, 512, 1408, 8);
  bindSize("height-range", "height-num", "height", syncSizeInputs, 512, 1408, 8);
  $("aspect-chips-v").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    state.p.width = Number(chip.dataset.w);
    state.p.height = Number(chip.dataset.h);
    syncVideoSize(); persist();
  });
  bindSize("vwidth-range", "vwidth-num", "width", syncVideoSize, 256, 1024, 16);
  bindSize("vheight-range", "vheight-num", "height", syncVideoSize, 256, 1024, 16);

  /* 图生图强度 / 视频帧数 / 音乐参数 */
  $("strength-range").addEventListener("input", () => { state.p.strength = Number($("strength-range").value); syncStrength(); updateComposerMeta(); persist(); });
  $("frames-range").addEventListener("input", () => { state.p.frames = snapFrames(Number($("frames-range").value)); syncFrames(); persist(); });
  $("bpm-range").addEventListener("input", () => { state.settings.bpm = Number($("bpm-range").value); syncMusic(); updateComposerMeta(); persist(); });
  $("music-dur-range").addEventListener("input", () => { state.settings.musicDuration = Number($("music-dur-range").value); syncMusic(); updateComposerMeta(); persist(); });

  /* steps / seed / negative / 风格 / 高级 */
  $("steps-range").addEventListener("input", () => { state.p.steps = Number($("steps-range").value); syncSteps(); persist(); });
  $("seed-lock").addEventListener("change", () => {
    state.p.seedLock = $("seed-lock").checked;
    if (state.p.seedLock && !Number.isInteger(state.p.seed)) { state.p.seed = randSeed(); }
    syncSeed(); persist();
  });
  $("seed-num").addEventListener("change", () => {
    const v = Number($("seed-num").value);
    state.p.seed = Number.isFinite(v) ? Math.max(0, Math.round(v)) : null;
    syncSeed(); persist();
  });
  $("seed-dice").addEventListener("click", () => { state.p.seed = randSeed(); state.p.seedLock = true; syncSeed(); persist(); });
  $("negative-input").addEventListener("input", () => { state.p.negative = $("negative-input").value; persist(); });
  /* 风格 / 曲风标签：再点一次从提示词里移除 */
  const bindSuffixChips = (container) => {
    container.addEventListener("click", (event) => {
      const chip = event.target.closest(".chip");
      if (!chip) return;
      const suffix = chip.dataset.suffix;
      if (els.prompt.value.includes(suffix)) {
        els.prompt.value = els.prompt.value
          .replace(", " + suffix, "").replace(suffix + ", ", "").replace(suffix, "")
          .replace(/[,，]\s*$/, "").trim();
      } else {
        const text = els.prompt.value.trim().replace(/[,，]\s*$/, "");
        els.prompt.value = text ? `${text}, ${suffix}` : suffix;
      }
      autosize();
      syncStyleChips();
      updateComposerMeta();
      els.prompt.focus();
      els.prompt.setSelectionRange(els.prompt.value.length, els.prompt.value.length);
    });
  };
  bindSuffixChips($("style-chips"));
  bindSuffixChips($("genre-chips"));
  $("advanced-json").addEventListener("blur", () => { state.p.advanced = $("advanced-json").value; persist(); });

  /* 引擎选择 / 重启（按当前模态存 engine / vengine） */
  els.engineSelect.addEventListener("change", () => {
    const key = MODALITY[state.mode] === "video" ? "vengine" : "engine";
    state.settings[key] = els.engineSelect.value;
    state.engineId = els.engineSelect.value;
    persist();
  });
  els.engineRestart.addEventListener("click", async () => {
    if (!state.engineId) return;
    els.engineRestart.disabled = true;
    els.engineState.textContent = "重启中（首次加载权重可能需要 1-2 分钟）…";
    try {
      await api(`/api/engines/${encodeURIComponent(state.engineId)}/restart`, { method: "POST" });
      showToast("引擎已重启", "success");
    } catch (error) {
      showToast(`引擎重启失败：${error.message}`, "error");
    } finally {
      els.engineRestart.disabled = false;
      refreshEngines();
    }
  });

  /* 对话流委托 */
  els.feed.addEventListener("click", async (event) => {
    const actionEl = event.target.closest("[data-act]");
    if (actionEl) {
      const { act, task } = actionEl.dataset;
      if (act === "cancel") await cancelTask(task);
      else if (act === "retry") await retryTask(task);
      else if (act === "upscale") await upscale(task);
      else if (act === "variation") await variation(task);
      return;
    }
    const img = event.target.closest("img[data-task]");
    if (img) {
      const tile = img.closest("[data-batch]");
      openLightbox(tile.dataset.batch, img.dataset.task);
    }
  });

  /* Lightbox */
  $("lb-close").addEventListener("click", closeLightbox);
  $("lightbox").addEventListener("click", (event) => { if (event.target.id === "lightbox") closeLightbox(); });
  $("lb-prev").addEventListener("click", () => lbShift(-1));
  $("lb-next").addEventListener("click", () => lbShift(1));
  $("lb-save").addEventListener("click", () => showToast("已触发下载（浏览器下载管理里查看）", "success"));
  $("lb-copy").addEventListener("click", async () => {
    const lb = state.lightbox;
    const rec = lb && state.tasks.get(lb.ids[lb.idx]);
    if (rec) {
      try { await navigator.clipboard.writeText(rec.prompt); showToast("提示词已复制", "success"); }
      catch { showToast("复制失败（浏览器权限）", "error"); }
    }
  });
  $("lb-u").addEventListener("click", () => {
    const lb = state.lightbox;
    if (!lb) return;
    closeLightbox();
    upscale(lb.ids[lb.idx]);
  });
  $("lb-v").addEventListener("click", () => {
    const lb = state.lightbox;
    if (!lb) return;
    closeLightbox();
    variation(lb.ids[lb.idx]);
  });
  document.addEventListener("keydown", (event) => {
    if (!state.lightbox) return;
    if (event.key === "Escape") closeLightbox();
    else if (event.key === "ArrowLeft") lbShift(-1);
    else if (event.key === "ArrowRight") lbShift(1);
  });
}

/* ------------------------------------------------------------------ 启动 */

function restoreUI() {
  const ui = loadJSON(LS.ui, {});
  if (ui.sideCollapsed) { document.body.classList.add("side-collapsed"); $("expand-side").classList.remove("hidden"); }
  if (ui.settingsHidden) document.body.classList.add("settings-hidden");
}

async function init() {
  const saved = loadJSON(LS.settings, {});
  state.settings = Object.assign(defaultSettings(), saved);
  /* v1（单一顶层参数）→ v2（按模式分桶）迁移：旧值并入文生图桶，不丢用户配置。 */
  const modes = {};
  for (const k of MODES) modes[k] = Object.assign(defaultModeParams(k), (saved.modes && saved.modes[k]) || {});
  if (!saved.modes) {
    for (const k of ["width", "height", "steps", "seedLock", "seed", "negative", "advanced"]) {
      if (saved[k] !== undefined) modes.t2i[k] = saved[k];
    }
  }
  state.settings.modes = modes;
  state.mode = MODES.includes(state.settings.mode) ? state.settings.mode : "t2i";
  state.p = state.settings.modes[state.mode];

  state.sessions = loadJSON(LS.sessions, []);
  state.activeId = loadJSON(LS.active, null);
  restoreUI();

  hydrateChrome();
  syncAll();
  bindEvents();

  renderSessions();
  renderFeed(true);
  await refreshHealth();
  await refreshEngines();
  await refreshTasks();
  setInterval(() => {
    if (document.hidden) return;
    refreshHealth();
    refreshEngines();
    refreshTasks();
  }, 2500);
}

init();
