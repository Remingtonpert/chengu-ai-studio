"use strict";

/* 辰鹄AI创作中心 前端 —— Midjourney Web 式交互，全部走现有 /api/* 与 /outputs/*。
 * 零外部依赖、离线可用；会话与参数保存在浏览器 localStorage，任务本体仍由后端 SQLite 队列管理。
 * 图生视频（i2v）走现有视频参数（init_image_path 作首帧），后端未改动。
 * 文生图 / 图生图 = Wan2.1 单帧档（num_frames=1），与视频共用 .venv-video 与权重；
 * AI 音乐 = ACE-Step 1.5。六个板块全部对应当前已注册的引擎。 */

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

/* 引擎 → 支持的任务类型（与 app/engine_registration.py 一致）。
   图片（wan-t2i / wan-i2i）是 Wan2.1 的单帧档，与视频共用同一套权重与 venv。 */
const ENGINE_TASK_TYPES = {
  "wan-t2i": ["image"], "wan-i2i": ["image"],
  "wan-t2v": ["video"], "wan-i2v": ["video"],
  "ace-step-15": ["music"],
};
/* 模式 → 引擎白名单：一个模式只认一个引擎，避免选错权重。 */
const MODE_ENGINE_ALLOW = {
  t2i: ["wan-t2i"], i2i: ["wan-i2i"],
  t2v: ["wan-t2v"], i2v: ["wan-i2v"],
  music: ["ace-step-15"],
};
const ENGINE_STATE_LABELS = { ready: "就绪", stopped: "未启动", starting: "启动中", failed: "异常", idle: "空闲" };
/* 视频（Wan2.1 1.3B）显存门槛（MB）：按 6GB 显存档位设计——bf16 权重 + 逐层 CPU 卸载，
   真机实测单卡峰值约 2021MB，引擎侧 app/engine/video/wan.py _SEQUENTIAL_MIN_FREE_MB = 1500。
   注意逐层卸载还需要约 16GB 空闲物理内存（UMT5 编码器 bf16 权重约 11.4GB 常驻内存）。
   总显存低于该门槛前端直接拦下提交。 */
const VIDEO_MIN_VRAM_MB = 6144;
const VIDEO_FREE_FLOOR_MB = 2000;   // 提交前要求的实测空闲显存，略高于引擎侧的 1500MB
/* 视频真实像素预算（与 app/engine/video/wan.py _MAX_PIXELS = 832*480*1.15 一致）：
   超预算的宽高会被引擎自动缩小，1.3B 档最高就是约 480p（约 0.46MP）。
   文生图 / 图生图走同一引擎的单帧档（num_frames=1），预算与显存门槛完全一致。 */
const VIDEO_MAX_PIXELS = 460800;
/* 音乐（ACE-Step 1.5，app/tasks/music_worker.py MUSIC_MIN_VRAM_MB）显存门槛（MB）：
   总显存低于 6GB 直接拦下提交；空闲门槛与 worker health_check 的 free gate 对齐（6144），
   因为 LM/DiT 走 CPU 卸载时的显存峰值只做过估算、尚未真机实测，宁可保守。 */
const MUSIC_MIN_VRAM_MB = 6144;
const MUSIC_FREE_FLOOR_MB = 6144;

/* 提交前的显存判定（网页端）——顺序很重要，别一上来就把用户支走：
   1) 先看一眼显存；2) 不够就先让后端把**本端自己拉起的**空闲引擎 worker 全停掉
   （显存当场交还），再实测一次；3) 还不够才拦下提交，并且点名是哪个程序占着显存。
   「还能用多少」用按进程实测的数：Windows 上 nvidia-smi 的 used 会把驱动/显示子系统
   保留的、以及进程已经结束但驱动没回收的都算进去，明显虚高，拿它当真实占用就会冤枉人。 */
async function vramFloorCheck(needFreeMb) {
  let snap = null;
  try {
    snap = await api("/api/hardware");
  } catch {
    return { ok: true, unknown: true }; // 探测失败不误判，交由后端 health_check 兜底
  }
  const gpu = snap && snap.gpu;
  if (!gpu) return { ok: true, noGpu: true };
  const freeOf = (g) => (typeof g.memory_free_effective_mb === "number"
    ? g.memory_free_effective_mb : g.memory_free_mb);
  if (freeOf(gpu) >= needFreeMb) return { ok: true, gpu };
  let current = gpu;
  let released = [];
  try {
    const r = await api("/api/engines/release-idle", { method: "POST" });
    released = (r && r.released) || [];
    if (r && r.gpu) current = r.gpu; // 释放后现查的数（含按进程实测的占用者）
  } catch { /* 释放失败就按现有数判定，不编数字 */ }
  if (freeOf(current) >= needFreeMb) return { ok: true, gpu: current, released };
  return {
    ok: false,
    gpu: current,
    released,
    free: freeOf(current),
    holders: current.memory_holders || "",
  };
}

/* 模式 Tab = 前端路由；后端接口按 task_type 分开，未改动。
   板块顺序与桌面端一致：AI 智能体 → 文生图 → 图生图 → 文生视频 → 图生视频 → AI音乐。 */
const MODES = ["agent", "t2i", "i2i", "t2v", "i2v", "music"];
const MODALITY = { t2i: "image", i2i: "image", t2v: "video", i2v: "video", music: "music" };
const MODE_LABELS = { agent: "AI 智能体", t2i: "文生图", i2i: "图生图", t2v: "文生视频", i2v: "图生视频", music: "AI音乐" };
const PLACEHOLDERS = {
  t2i: "描述你想要的画面…（Wan2.1-T2V-1.3B 单帧档 · 最高约 480p · 中英文提示词都认）",
  i2i: "先放参考图，再描述想改成什么样…（Wan2.1-VACE-1.3B 按垫图参考重绘）",
  t2v: "描述动态画面…（Wan2.1-T2V-1.3B · 16fps mp4 · 最高约 480p）",
  i2v: "先放首帧图片，再描述画面如何运动…（Wan2.1-I2V-1.3B · 16fps mp4）",
  music: "描述音乐：风格、情绪、乐器、节奏…（ACE-Step 1.5 · 10-480 秒 · 可带中文/英文人声）",
};
/* 生成参数白名单（app/tasks/video_worker.py：width,height,steps,seed,negative_prompt；
   i2v / i2i 再加 init_image_path；num_frames 只属于视频）。 */
const VIDEO_ALLOWED_PARAMS = new Set([
  "width", "height", "num_frames", "steps", "seed", "negative_prompt", "init_image_path",
]);
const NEEDS_REF = new Set(["i2i", "i2v"]);
const VIDEO_FPS = 16;            // Wan2.1 原生帧率（mp4 导出固定 16fps）
const snapFrames = (v) => { const n = clampInt(v, 5, 161); return n - ((n - 1) % 4); };
const isVideoUrl = (u) => /\.(mp4|webm|mov|mkv)$/i.test(u || "");
/* 历史兜底：已下架板块的音频 / 图片产物仍要能显示与另存，这两个判断保留。 */
const isAudioUrl = (u) => /\.(wav|mp3|flac|ogg|m4a|aac)$/i.test(u || "");

const state = {
  sessions: [],
  activeId: null,
  settings: null,
  mode: "agent",
  p: null,                    // = settings.modes[state.mode]：当前模式的参数桶
  engines: [],
  engineId: "wan-t2v",
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
  /* 图片与视频共用 Wan 单引擎：默认尺寸落在引擎真实像素预算内（约 480p 档，
     见 app/engine/video/wan.py _MAX_PIXELS）；音乐档不用尺寸，steps 用 turbo 默认 8。
     「AI 智能体」页不用参数面板，借用一个视频参数桶保持结构一致。 */
  const steps = mode === "music" ? 8 : 28;
  return { width: 832, height: 480, steps, frames: 33, seedLock: false, seed: null, negative: "", advanced: "" };
}
function defaultSettings() {
  const modes = {};
  for (const m of MODES) modes[m] = defaultModeParams(m);
  return {
    mode: "agent",
    refDir: "",
    vengine: "",                   // 图片 + 视频模态引擎（wan-t2i / wan-i2i / wan-t2v / wan-i2v，按标签页白名单自动落位）
    mengine: "",                   // 音乐模态引擎（ace-step-15）
    bpm: 120,                      // 音乐参数：与桌面端 param_panel 同名同范围（30-300，面板滑杆 60-180）
    musicDuration: 180,            // 目标时长（秒），worker 侧 10-480
    instrumental: false,           // 纯器乐（不要人声）
    vocalLanguage: "zh",           // 人声语言：只支持 zh / en
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

/* ------------------------------------------------- 产物另存到本地硬盘（自选位置）
   产物始终先写进项目内 output 目录（本工具里即时显示的就是那份，队列也按它管理）；
   这里只负责「再复制一份给用户挑的地方」：
   - Edge / Chrome 支持 showSaveFilePicker，弹真正的「另存为」对话框，可选任意盘符；
   - 不支持的浏览器退回普通下载（服务端 ?download=1，落到浏览器下载目录；
     在浏览器设置里打开「每次询问保存位置」同样能自由选位置）。
   自动另存目录由服务端按配置在生成完成时复制，跟这里手动另存互不影响。 */
const SAVE_MIME_BY_EXT = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".bmp": "image/bmp",
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".wav": "audio/wav",
  ".mp3": "audio/mpeg",
};

function mainArtifact(rec) {
  const files = (rec && rec.output_files ? rec.output_files : []).filter((f) => f.url);
  if (!files.length) return null;
  if (rec.task_type === "video") return files.find((f) => isVideoUrl(f.url)) || files[0];
  /* 历史兼容：已下架板块遗留的 music / image 产物仍要能显示与另存。 */
  if (rec.task_type === "music") return files.find((f) => isAudioUrl(f.url)) || files[0];
  return files.find((f) => /\.(png|jpe?g|webp|bmp)$/i.test(f.name || f.url)) || files[0];
}

async function saveArtifactToDisk(file) {
  if (!file || !file.url) {
    showToast("该产物不在输出目录里，无法另存", "error");
    return false;
  }
  const name = file.name || "chengu-output";
  const ext = (name.match(/\.[A-Za-z0-9]+$/) || [""])[0].toLowerCase();
  const picker = window.showSaveFilePicker;
  if (typeof picker === "function") {
    let handle = null;
    try {
      const options = { suggestedName: name };
      const mime = SAVE_MIME_BY_EXT[ext];
      if (mime) options.types = [{ description: "生成产物", accept: { [mime]: [ext] } }];
      handle = await picker(options);
    } catch (error) {
      if (error && error.name === "AbortError") return false;   // 用户自己取消：不擅自改成下载
    }
    if (handle) {
      try {
        const blob = await (await fetch(file.url)).blob();
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
        showToast(`已另存为 ${handle.name}（你选的位置）`, "success");
        return true;
      } catch (error) {
        showToast(`另存失败：${error.message}`, "error");
        return false;
      }
    }
  }
  const a = document.createElement("a");
  a.href = file.url + (file.url.includes("?") ? "&" : "?") + "download=1";
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  showToast("已触发下载（在浏览器下载管理里查看；开启「每次询问保存位置」即可自选目录）", "success");
  return true;
}

async function saveTaskArtifact(taskId) {
  const rec = state.tasks.get(taskId);
  if (!rec) { showToast("任务未找到（可能被后端清理）", "error"); return; }
  const file = mainArtifact(rec);
  if (!file) { showToast("该任务没有可另存的产物", "error"); return; }
  await saveArtifactToDisk(file);
}

function savedCopiesNode(rec) {
  const copies = (rec && rec.saved_copies) || [];
  if (!copies.length) return null;
  const dir = String(copies[0]).replace(/[\\/][^\\/]*$/, "");
  return el("div", "tile-foot muted", `已自动另存 ${copies.length} 份 → ${dir}`);
}

async function loadSaveSettings() {
  try {
    const data = await api("/api/settings");
    $("output-dir-label").textContent = data.output_dir || "（未知）";
    $("project-output-label").textContent = data.project_output_dir || "（未知）";
    $("autosave-dir").value = data.auto_save_dir || "";
    $("autosave-state").textContent = data.auto_save_dir
      ? `已开启：${data.auto_save_dir}`
      : "未开启自动另存（只存上面两处）";
  } catch (error) {
    $("output-dir-label").textContent = "读取失败";
    $("autosave-state").textContent = `读取保存设置失败：${error.message}`;
  }
}

async function saveSaveSettings() {
  const dir = $("autosave-dir").value.trim();
  $("autosave-save").disabled = true;
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ auto_save_dir: dir }),
    });
    $("autosave-dir").value = data.auto_save_dir || "";
    $("autosave-state").textContent = data.auto_save_dir
      ? `已开启：${data.auto_save_dir}`
      : "未开启自动另存（只存上面两处）";
    showToast(dir ? "自动另存目录已保存（两端同时生效）" : "已关闭自动另存（产物只存输出目录与工具内 output）", "success");
  } catch (error) {
    showToast(`保存设置失败：${error.message}`, "error");
  } finally {
    $("autosave-save").disabled = false;
  }
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

function syncVideoSize() {
  const s = state.p;
  $("vwidth-range").value = Math.min(832, Math.max(256, s.width));
  $("vheight-range").value = Math.min(832, Math.max(256, s.height));
  $("vwidth-num").value = s.width;
  $("vheight-num").value = s.height;
  for (const chip of $("aspect-chips-v").children) {
    chip.classList.toggle("on", Number(chip.dataset.w) === s.width && Number(chip.dataset.h) === s.height);
  }
  const px = s.width * s.height;
  const f = snapFrames(s.frames ?? 33);
  const hint = $("v-vram-hint");
  const isImage = MODALITY[state.mode] === "image";
  if (px > VIDEO_MAX_PIXELS) {
    hint.className = "hint warn";
    hint.textContent = `${s.width}×${s.height}（${(px / 1e6).toFixed(2)}MP）超出 Wan2.1 1.3B 的像素预算 ${VIDEO_MAX_PIXELS}px（约 0.46MP，≈480p）：引擎会等比缩小到预算内，产物实际分辨率以引擎为准。`;
  } else if (isImage) {
    hint.className = "hint";
    hint.textContent = `图片像素 ${(px / 1e6).toFixed(2)}MP · 与视频同一个 Wan 引擎的单帧档（num_frames=1），按 6GB 显存档位设计（bf16 + 逐层 CPU 卸载；单帧峰值应明显低于视频实测的约 2GB，未实测）；逐层卸载还需要约 16GB 空闲物理内存。`;
  } else {
    hint.className = "hint";
    hint.textContent = `视频像素 ${(px / 1e6).toFixed(2)}MP · 按 6GB 显存档位设计（bf16 + 逐层 CPU 卸载，实测峰值约 2GB）；逐层卸载还需要约 16GB 空闲物理内存。帧数越长越吃显存与时间（当前 ${f} 帧 @16fps）。`;
  }
  updateComposerMeta();
}

function syncFrames() {
  const f = snapFrames(state.p.frames ?? 33);
  state.p.frames = f;
  $("frames-range").value = f;
  $("frames-label").textContent = `${(f / VIDEO_FPS).toFixed(2)} 秒 · ${f} 帧 @16fps`;
  updateComposerMeta();
}

function syncSteps() {
  const range = $("steps-range");
  range.max = "50";
  range.value = Math.min(Number(range.max), Math.max(1, state.p.steps));
  $("steps-label").textContent = `${state.p.steps} 步`;
  const modality = MODALITY[state.mode];
  if (modality === "music") {
    $("steps-hint").textContent = "音乐 turbo 权重默认 8 步（推荐）；最多 50 步，步数越多等待越长。";
  } else if (modality === "image") {
    $("steps-hint").textContent = "图片推荐 28 步（默认）；6GB 上步数直接决定等待时长。";
  } else {
    $("steps-hint").textContent = "视频推荐 28 步（默认）；6GB 上步数直接决定等待时长。";
  }
  updateComposerMeta();
}

function syncSeed() {
  $("seed-lock").checked = state.p.seedLock;
  $("seed-num").value = state.p.seed ?? "";
  $("seed-num").disabled = !state.p.seedLock;
  const modality = MODALITY[state.mode];
  const what = modality === "music" ? "同一段音乐" : modality === "image" ? "同一张图" : "同一段视频";
  $("seed-hint").textContent = `不固定 = 每次随机 seed；固定 = 可复现${what}。`;
  updateComposerMeta();
}

/* 音乐档参数：BPM / 目标时长 / 纯器乐 / 人声语言。范围与 app/tasks/music_worker.py 对齐
   （bpm 30-300、时长 10-480 秒），面板滑杆只给常用区间（60-180 / 30-480）。 */
function syncMusic() {
  const s = state.settings;
  s.bpm = clampInt(s.bpm ?? 120, 30, 300);
  s.musicDuration = clampInt(s.musicDuration ?? 180, 10, 480);
  if (s.vocalLanguage !== "en") s.vocalLanguage = "zh";
  $("bpm-range").value = Math.min(180, Math.max(60, s.bpm));
  $("bpm-label").textContent = `${s.bpm} BPM`;
  $("music-dur-range").value = s.musicDuration;
  $("music-dur-label").textContent = `${s.musicDuration} 秒`;
  const inst = $("instrumental-toggle");
  if (inst) inst.classList.toggle("on", Boolean(s.instrumental));
  const lang = $("vocal-lang-select");
  if (lang) lang.value = s.vocalLanguage;
  updateComposerMeta();
}

/* 切换模式后把该模式参数桶灌回共享控件。音乐模态不碰尺寸/负面/高级参数
   （那些分区在音乐板块里本来就按 data-modes 收起）。 */
function syncAll() {
  if (!MODALITY[state.mode]) { updateComposerMeta(); return; }
  if (MODALITY[state.mode] === "music") {
    syncSteps();
    syncSeed();
    syncMusic();
    updateComposerMeta();
    return;
  }
  syncVideoSize();
  syncSteps();
  syncSeed();
  syncFrames();
  $("negative-input").value = state.p.negative;
  $("advanced-json").value = state.p.advanced || "";
  $("advanced-hint").textContent = "白名单：width height num_frames steps seed negative_prompt init_image_path；num_frames 只对视频板块生效；init_image_path 需为已存在的具体图片文件绝对路径（不能是文件夹）";
  updateComposerMeta();
}

/* ------------------------------------------------------------------ 模式 Tab 路由 */

function commitPanelText() {
  if (!state.p) return;
  state.p.negative = $("negative-input").value;
  state.p.advanced = $("advanced-json").value;
}

const IMAGINE_LABELS = { agent: "生成视频", t2i: "想象", i2i: "想象（图生图）", t2v: "生成视频", i2v: "生成视频", music: "生成音乐" };

function hydrateChrome() {
  const mode = state.mode;
  /* 规则 5：每个在岗板块一套外观（AI 智能体 ChatGPT 风 / 图片、视频、音乐 PIKA 风）。
     皮肤全在 style.css 的 html[data-mode=...] 块里，这里只负责把当前模式挂到 <html> 上。 */
  document.documentElement.dataset.mode = mode;
  /* 参数面板：按 data-modes 显隐分区 */
  for (const sec of document.querySelectorAll("section[data-modes]")) {
    const modes = (sec.dataset.modes || "").split(/\s+/);
    sec.classList.toggle("hidden", !modes.includes(mode));
  }
  /* 风格 chip 只给图片 / 视频，曲风 chip 只给音乐（同在一个分区里换一组） */
  $("style-chips").classList.toggle("hidden", MODALITY[mode] !== "image" && MODALITY[mode] !== "video");
  $("genre-chips").classList.toggle("hidden", MODALITY[mode] !== "music");
  /* 垫图区：图生图要参考图，图生视频要首帧 */
  els.refZone.classList.toggle("hidden", !NEEDS_REF.has(mode));
  els.refZoneText.textContent = mode === "i2i"
    ? "垫图（必需）：点“选择图片”、拖入或粘贴图片"
    : "首帧图片（必需）：选择/拖入/粘贴首帧，再描述运动";
  els.prompt.placeholder = PLACEHOLDERS[mode] || PLACEHOLDERS.t2v;
  els.imagineBtn.textContent = IMAGINE_LABELS[mode] || "生成视频";
  renderTabs();
}

function setMode(mode) {
  if (!MODES.includes(mode) || mode === state.mode) return;
  commitPanelText();
  state.mode = mode;
  state.settings.mode = mode;
  if (!state.settings.modes[mode]) state.settings.modes[mode] = defaultModeParams();
  state.p = state.settings.modes[mode];
  clearRef();
  hydrateChrome();
  syncAll();
  persist();
  refreshEngines();
  /* 对话流按板块过滤（md 第 32 条）：切板块必须重画一次，否则显示的还是上一板块的内容。 */
  renderFeed(true);
  els.prompt.focus();
}

const REF_IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".webp", ".bmp"];

/* 浏览器无法探测磁盘，但目录尾巴与不支持的扩展名可以先行拦截；
   文件是否真实存在由后端 _submit_task 复核。 */
function refShapeError(raw) {
  const path = String(raw || "").trim();
  if (!path) return "参考图需要本机完整路径，例如 D:\\ChenguAI\\uploads\\photo.png";
  if (!/^[A-Za-z]:[\\/]/.test(path)) {
    return "参考图路径必须是盘符开头的绝对路径，例如 D:\\ChenguAI\\uploads\\photo.png；收到的是：" + path;
  }
  if (/[\\/]+$/.test(path)) {
    return "垫图请选择具体图片文件，不能是文件夹：" + path;
  }
  const name = path.slice(Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/")) + 1);
  const dot = name.lastIndexOf(".");
  const suffix = dot > 0 ? name.slice(dot).toLowerCase() : "";
  if (!REF_IMAGE_SUFFIXES.includes(suffix)) {
    return "垫图格式需为 png / jpg / jpeg / webp / bmp；收到的是：" + (name || path);
  }
  return null;
}

function parseAdvanced() {
  const text = (state.p.advanced || "").trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("必须是 JSON 对象");
    const clean = {};
    const rejected = [];
    for (const [key, value] of Object.entries(parsed)) {
      if (!VIDEO_ALLOWED_PARAMS.has(key)) { rejected.push(key); continue; }
      if (key === "init_image_path") {
        const bad = refShapeError(value);
        if (bad) { showToast("高级参数 " + bad, "error"); continue; }
      }
      clean[key] = value;
    }
    if (rejected.length) showToast(`高级参数被忽略（不在白名单）：${rejected.join(", ")}`, "error");
    return clean;
  } catch (error) {
    showToast(`高级参数 JSON 无效：${error.message}`, "error");
    return {};
  }
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

/* 图片档参数：与视频同一引擎，只是不传 num_frames（单帧档由引擎固定 num_frames=1，
   worker 的图片白名单也不收 num_frames）；i2i 的参考图同样走 init_image_path。 */
function collectImageParams(refPath) {
  const s = state.p;
  const params = { width: s.width, height: s.height, steps: s.steps };
  if (s.negative.trim()) params.negative_prompt = s.negative.trim();
  params.seed = (s.seedLock && Number.isInteger(s.seed)) ? s.seed : randSeed();
  Object.assign(params, parseAdvanced());
  delete params.num_frames;
  if (refPath) params.init_image_path = refPath;
  return params;
}

function updateComposerMeta() {
  const modality = MODALITY[state.mode];
  let bits;
  if (modality === "music") {
    const s = state.settings;
    bits = [`BPM ${clampInt(s.bpm ?? 120, 30, 300)}`, `目标时长 ${clampInt(s.musicDuration ?? 180, 10, 480)} 秒`,
      `steps ${state.p.steps}`, state.p.seedLock ? `seed ${state.p.seed}` : "seed 随机",
      s.instrumental ? "纯器乐" : `人声 ${s.vocalLanguage === "en" ? "英文" : "中文"}`];
  } else if (modality === "image") {
    bits = [`图片 ${state.p.width}×${state.p.height}`, `steps ${state.p.steps}`,
      state.p.seedLock ? `seed ${state.p.seed}` : "seed 随机"];
  } else {
    const f = snapFrames(state.p.frames ?? 33);
    bits = [`视频 ${state.p.width}×${state.p.height}`, `${(f / VIDEO_FPS).toFixed(2)}s · ${f} 帧`,
      `steps ${state.p.steps}`, state.p.seedLock ? `seed ${state.p.seed}` : "seed 随机"];
  }
  if (NEEDS_REF.has(state.mode)) bits.push(state.ref ? (state.ref.path ? "垫图：已挂" : "垫图：待确认路径") : "垫图：未挂");
  els.composerMeta.textContent = bits.join(" · ");
  syncStyleChips();
}

/* 风格标签（图片/视频）与曲风标签（音乐）：勾选态 = 提示词里是否包含该文本。 */
function syncStyleChips() {
  const text = els.prompt.value;
  for (const chip of $("style-chips").children) chip.classList.toggle("on", text.includes(chip.dataset.suffix));
  for (const chip of $("genre-chips").children) chip.classList.toggle("on", text.includes(chip.dataset.suffix));
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

/* AI 智能体板块不绑定某一个引擎：后端按板块自动挑引擎（文生图 wan-t2i / 图生图 wan-i2i、
   文生视频 wan-t2v / 图生视频 wan-i2v、音乐 ace-step-15，见 app/agent/service.py）。
   早期版本在这里按板块筛选引擎，AI 智能体没有 modality，筛完只剩空列表，
   于是顶部胶囊固定显示红色的「无引擎」，和桌面端完全不一样。现在改为显示全部引擎的概况。 */
function renderAgentEngineOverview() {
  const total = state.engines.length;
  els.engineSelect.textContent = "";
  const opt = el("option", null, total ? "按板块自动选择" : "未注册任何生成引擎");
  opt.disabled = true;
  els.engineSelect.appendChild(opt);
  els.engineSelect.disabled = !total;
  els.engineRestart.disabled = true;
  state.engineId = "";
  if (!total) {
    els.enginePill.textContent = "无引擎";
    els.enginePill.className = "pill bad";
    els.engineState.textContent = "没有注册任何生成引擎（检查 engines_enabled）";
    return;
  }
  const rank = { failed: 0, starting: 1, busy: 2, ready: 3, idle: 4, stopped: 5 };
  let worst = "stopped";
  for (const engine of state.engines) {
    if ((rank[engine.state] ?? 9) < rank[worst]) worst = engine.state;
  }
  const started = state.engines.filter((engine) => engine.state && engine.state !== "stopped").length;
  els.enginePill.textContent = `${total} 个引擎 · ${ENGINE_STATE_LABELS[worst] || worst}`;
  els.enginePill.className = "pill " + (worst === "failed" ? "bad" : started ? "ok" : "");
  els.engineState.textContent =
    `${total} 个引擎已注册，其中 ${started} 个已启动。AI 智能体会按板块自动选择引擎，` +
    "已注册未启动不等于没有引擎：需要时点「启动」或让智能体自动拉起。";
}

async function refreshEngines() {
  let payload;
  try { payload = await api("/api/engines"); } catch { return; }
  state.engines = payload.engines || [];
  const modality = MODALITY[state.mode];
  if (!modality) {
    renderAgentEngineOverview();
    return;
  }
  els.engineRestart.disabled = false;
  els.engineSelect.disabled = false;
  const allow = MODE_ENGINE_ALLOW[state.mode];
  const options = state.engines.filter((e) => {
    const types = ENGINE_TASK_TYPES[e.engine_id];
    return (!types || types.includes(modality)) && (!allow || allow.includes(e.engine_id));
  });
  els.engineSelect.textContent = "";
  if (!options.length) {
    if (modality === "music") {
      els.engineSelect.appendChild(el("option", null, "音乐引擎未注册 · 需 .venv-music 与 models\\ace-step-1.5"));
      els.engineState.textContent = "音乐引擎未注册：需 .venv-music（运行 scripts\\setup_music_venv.ps1）与 models\\ace-step-1.5 权重（运行 scripts\\download_music_weights.py，MIT），未就绪前不会提交、不伪造输出";
    } else if (modality === "image") {
      els.engineSelect.appendChild(el("option", null, "图片引擎未注册 · 需 .venv-video 与 models\\wan2.1"));
      els.engineState.textContent = "图片引擎未注册：图片档与视频共用 .venv-video（运行 scripts\\setup_video_venv.ps1）与 models\\wan2.1 权重（运行 scripts\\download_video_weights.py，Apache-2.0），未就绪前不会提交、不伪造输出";
    } else {
      els.engineSelect.appendChild(el("option", null, "视频引擎未注册 · 需 .venv-video 与 models\\wan2.1"));
      els.engineState.textContent = "视频引擎未注册：需 .venv-video（运行 scripts\\setup_video_venv.ps1）与 models\\wan2.1 权重（运行 scripts\\download_video_weights.py，Apache-2.0），未就绪前不会提交、不伪造输出";
    }
    els.enginePill.textContent = "无引擎"; els.enginePill.className = "pill bad";
    state.engineId = "";
    return;
  }
  // 引擎槽按模态存：音乐档存 mengine，图片 / 视频档共用 vengine（与桌面端 param_panel 同名）
  const engineKey = modality === "music" ? "mengine" : "vengine";
  if (!options.some((e) => e.engine_id === state.settings[engineKey])) state.settings[engineKey] = options[0].engine_id;
  state.engineId = state.settings[engineKey];
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
  if (hint) hint.textContent = engineModeHint(modality);
  els.engineState.textContent = current ? (current.message || label) : label;
}

/* 与桌面端 app/ui/widgets/param_panel.py ENGINE_MODE_HINTS 同一份口径：
   视频档的 2021MB 是真机实测；图片 / 音乐档的 6GB 是设计预期，尚未实测（验收看
   任务元数据里的 peak_vram_mb），文案里必须写清楚，不许冒充实测。 */
function engineModeHint(modality) {
  if (modality === "music") {
    return "ACE-Step 1.5（MIT，本地离线，无云端 API）：48kHz wav，带人声（中英文）或纯器乐，10-480 秒。"
      + "按 6GB 显存档位设计：5Hz 语言模型与 DiT 走 CPU 卸载，峰值显存为预期值（未实测，验收看任务元数据 peak_vram_mb）；"
      + "turbo 默认 8 步。权重在 models\\ace-step-1.5（约 9.4GB，占物理内存），首次加载 1-3 分钟。"
      + "与图片/视频共用 FIFO 串行队列。"
      + `提交前本端会先把自己拉起的空闲引擎释放掉再实测空闲显存，空闲低于 ${MUSIC_FREE_FLOOR_MB}MB 会拦下提交并点名占用者。`;
  }
  if (modality === "image") {
    return "Wan2.1-T2V-1.3B 单帧出图（Apache-2.0，本地离线）：wan-t2i 直接渲染 / wan-i2i 用 VACE 参考重绘，输出 PNG。"
      + "与视频共用 .venv-video 与 models\\wan2.1 权重：bf16 + 逐层 CPU 卸载，按 6GB 显存档位设计"
      + "（单帧 token 约为 81 帧视频的 1/13，预期峰值低于视频档的实测 2021MB，图片档本身尚未实测，验收看任务元数据 peak_vram_mb）；"
      + "逐层卸载需约 16GB 空闲物理内存。分辨率停在 480p 档（宽高 16 的倍数，超预算引擎会自动缩到档位上限），一次 1 张。"
      + `提交前本端会先把自己拉起的空闲引擎与大模型释放掉再实测空闲显存，空闲低于 ${VIDEO_FREE_FLOOR_MB}MB 会拦下提交并点名是哪个程序占着显存；`
      + "要手动清可以跑 scripts\\release_local_engine.bat。";
  }
  return "Wan2.1 1.3B（Apache-2.0，本地离线）：文生视频 wan-t2v / 图生视频 wan-i2v（VACE），导出 16fps mp4，"
    + "分辨率最高约 480p（约 0.46MP，要 1080p 需要另配超分或更大的模型），与智能体排的任务共用 FIFO 串行队列。"
    + "显存按 6GB 档位设计：bf16 权重 + 逐层 CPU 卸载，真机实测单卡峰值约 2GB；逐层卸载还需要约 16GB 空闲物理内存。"
    + `提交前本端会先把自己拉起的空闲引擎与大模型释放掉再实测空闲显存，空闲低于 ${VIDEO_FREE_FLOOR_MB}MB 会拦下提交并点名是哪个程序占着显存；`
    + "要手动清可以跑 scripts\\release_local_engine.bat。";
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

/* 「AI 智能体」板块的角标：只数智能体自己排进队列的任务（本地批次上打了 agent 标记的那批）。 */
function agentActiveCount() {
  let n = 0;
  for (const session of state.sessions) {
    for (const batch of session.batches) {
      if (!batch.agent) continue;
      for (const id of batch.taskIds) {
        const rec = state.tasks.get(id);
        if (rec && (rec.status === "queued" || rec.status === "preparing" || rec.status === "running")) n++;
      }
    }
  }
  return n;
}

function renderTabs() {
  for (const tab of els.modeTabs.children) {
    tab.classList.toggle("on", tab.dataset.mode === state.mode);
    const badge = tab.querySelector(".tab-badge");
    if (!badge) continue;
    const n = tab.dataset.mode === "agent" ? agentActiveCount() : activeCount(MODALITY[tab.dataset.mode]);
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
    `seed ${p.seed ?? "—"} · ${p.width ?? "?"}×${p.height ?? "?"} · ${frames ? `${(frames / VIDEO_FPS).toFixed(2)}s @16fps` : "时长?"}`));
  const vid = files.find((f) => isVideoUrl(f.url));
  if (vid) {
    const a = el("a", "vu dl", "⬇下载视频");
    a.href = vid.url + (vid.url.includes("?") ? "&" : "?") + "download=1";
    a.download = vid.name || "辰鹄视频.mp4";
    const acts = el("div", "tile-acts");
    const btnSave = el("button", "vu"); btnSave.innerHTML = "<b>⬇</b>另存到硬盘";
    btnSave.dataset.act = "save"; btnSave.dataset.task = rec.task_id;
    acts.append(a, btnSave);
    tile.appendChild(acts);
  }
  const note = savedCopiesNode(rec);
  if (note) tile.appendChild(note);
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
  dl.href = item.url + (item.url.includes("?") ? "&" : "?") + "download=1";
  dl.download = item.name || "辰鹄音乐.wav";
  const acts = el("div", "tile-acts");
  const btnSave = el("button", "vu"); btnSave.innerHTML = "<b>⬇</b>另存到硬盘";
  btnSave.dataset.act = "save"; btnSave.dataset.task = rec.task_id;
  acts.append(dl, btnSave);
  tile.appendChild(acts);
  const note = savedCopiesNode(rec);
  if (note) tile.appendChild(note);
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
    const btnSave = el("button", "vu"); btnSave.innerHTML = "<b>⬇</b>另存";
    btnSave.dataset.act = "save"; btnSave.dataset.task = taskId;
    acts.append(btnV, btnU, btnSave);
    tile.appendChild(acts);
    const note = savedCopiesNode(rec);
    if (note) tile.appendChild(note);
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
  if (rec.task_type === "video") label.appendChild(el("span", "muted", "视频管线：Wan2.1 时空去噪 → VAE 解码 → mp4 编码，渲染需数分钟到数十分钟"));
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
      engine_id: engineId || state.settings[taskType === "music" ? "mengine" : "vengine"],
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
  if (modality === "video" || modality === "image") {
    const want = modality;
    const label = modality === "video" ? "视频" : "图片";
    if (!state.engines.some((e) => (ENGINE_TASK_TYPES[e.engine_id] || []).includes(want))) {
      showToast(`Wan2.1 ${label}引擎未注册：需 .venv-video（运行 scripts\\setup_video_venv.ps1）与 models\\wan2.1 权重（运行 scripts\\download_video_weights.py，Apache-2.0）；未就绪前不会提交任务，也不会伪造输出。`, "error");
      return;
    }
    let vhw = null;
    try { vhw = await api("/api/hardware"); } catch { /* 探测失败不误判，交由后端 health_check 兜底 */ }
    if (vhw && !vhw.gpu) {
      showToast(`显存不足，无法启动 Wan2.1 ${label}模型：检测不到 NVIDIA 显卡（最低要求 6GB 显存档位）。`, "error");
      return;
    }
    if (vhw && vhw.gpu && vhw.gpu.memory_total_mb < VIDEO_MIN_VRAM_MB) {
      showToast(`显存不足，无法启动 Wan2.1 ${label}模型：显卡 ${vhw.gpu.name || "?"} 仅 ${vhw.gpu.memory_total_mb} MB，最低要求 ${VIDEO_MIN_VRAM_MB} MB（6GB 档）。`, "error");
      return;
    }
    const vFloor = await vramFloorCheck(VIDEO_FREE_FLOOR_MB);
    if (!vFloor.ok) {
      const gave = vFloor.released && vFloor.released.length
        ? `本端已经先把自己拉起的空闲引擎释放掉了（${vFloor.released.join("、")}）；`
        : "本端没有可释放的空闲引擎；";
      showToast(`显存不足，无法启动 Wan2.1 ${label}模型：按进程实测还能用的空闲显存只有 ${vFloor.free} MB（6GB 档起跑需约 ${VIDEO_FREE_FLOOR_MB} MB 空闲）。${gave}显存目前主要被 ${vFloor.holders || "别的程序"} 占着——退出占显存的程序再提交。（这里不是按 nvidia-smi 的「已用」算的：Windows 上那个数把驱动保留的、以及已经结束还没回收的进程也算进去，明显偏大。）`, "error");
      return;
    }
  }
  if (!NEEDS_REF.has(state.mode) && state.ref) clearRef(); // 文生图/文生视频不挂垫图
  let extraParams = {};
  if (state.ref) {
    const path = state.ref.path.trim();
    if (!path) { showToast("参考图需要本机完整路径（浏览器拿不到剪贴板图片的磁盘路径）。填路径后再提交，或点 ✕ 取消引用。", "error"); return; }
    if (!/^[A-Za-z]:[\\/]/.test(path)) { showToast("参考图路径必须是盘符开头的绝对路径，例如 D:\\ChenguAI\\uploads\\photo.png", "error"); return; }
    const shapeError = refShapeError(path);
    if (shapeError) { showToast(shapeError, "error"); return; }
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
        // 立刻把刚排队的任务放进本地缓存，卡片当场出现「排队中」，不用等下一次轮询。
        state.tasks.set(rec.task_id, rec);
        showToast(refPath
          ? "图生视频已排队：Wan2.1-VACE 从首帧生成（16fps mp4），首次生成需加载权重，通常 5-15 分钟"
          : "文生视频已排队：Wan2.1-T2V-1.3B 直接渲染（16fps mp4），首次生成需加载权重，通常 5-15 分钟", "success");
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
    const base = collectImageParams(refPath); // 单帧档：一次 1 张，参数不带 num_frames / strength
    const fullPrompt = text; // 风格标签已直接内联进提示词，不再另行追加
    const batch = {
      id: uid(), kind: "imagine", mode: state.mode, prompt: text,
      time: formatTime(new Date().toISOString()),
      refPath, params: base, taskIds: [],
    };
    session.batches.push(batch);
    try {
      const rec = await submitTask(fullPrompt, base, "image", state.settings.vengine);
      batch.taskIds.push(rec.task_id);
      // 立刻把刚排队的任务放进本地缓存，卡片当场出现「排队中」，不用等下一次轮询。
      state.tasks.set(rec.task_id, rec);
      showToast(refPath
        ? "图生图已排队：Wan2.1-VACE 按垫图参考重绘（单帧 PNG，480p 档），首次生成需加载权重，通常数分钟"
        : "文生图已排队：Wan2.1-T2V-1.3B 单帧渲染（PNG，480p 档），首次生成需加载权重，通常数分钟", "success");
    } catch (error) {
      showToast(`图片提交失败：${error.message}`, "error");
    }
    renderFeed(true);
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
 * 不向后端抛任务；再弹窗确认能力边界（10-480 秒带人声/纯器乐，按 6GB 档设计、
 * 峰值为预期值未实测；不支持超长多轨道复杂编曲）。
 * 队列与图片/视频共用 FIFO 串行，不并发抢占显存。 */
async function imagineMusic(text) {
  if (!state.engines.some((e) => (ENGINE_TASK_TYPES[e.engine_id] || []).includes("music"))) {
    showToast("音乐引擎未注册：需 .venv-music 与 vendor\\ACE-Step-1.5（运行 scripts\\setup_music_venv.ps1）及权重下载；引擎未就绪前不会提交，也不会伪造输出。", "error");
    return;
  }
  let hw = null;
  try { hw = await api("/api/hardware"); } catch { /* 探测失败不误判，交由后端 health_check 兜底 */ }
  if (hw && !hw.gpu) {
    showToast("显存不足，无法启动ACE-Step1.5音乐模型：检测不到 NVIDIA 显卡（最低要求 6GB 显存档位）。", "error");
    return;
  }
  if (hw && hw.gpu && hw.gpu.memory_total_mb < MUSIC_MIN_VRAM_MB) {
    showToast(`显存不足，无法启动ACE-Step1.5音乐模型：显卡 ${hw.gpu.name || "?"} 仅 ${hw.gpu.memory_total_mb} MB，最低要求 ${MUSIC_MIN_VRAM_MB} MB（6GB 档）。`, "error");
    return;
  }
  const mFloor = await vramFloorCheck(MUSIC_FREE_FLOOR_MB);
  if (!mFloor.ok) {
    const gave = mFloor.released && mFloor.released.length
      ? `本端已经先把自己拉起的空闲引擎释放掉了（${mFloor.released.join("、")}）；`
      : "本端没有可释放的空闲引擎；";
    showToast(`显存不足，无法启动ACE-Step1.5音乐模型：按进程实测还能用的空闲显存只有 ${mFloor.free} MB（引擎 health_check 要求空闲在 6GB 档水位）。${gave}显存目前主要被 ${mFloor.holders || "别的程序"} 占着——退出占显存的程序再提交。（不是按 nvidia-smi 的「已用」算的，那个数在 Windows 上偏大。）`, "error");
    return;
  }
  syncMusic(); // 把面板值夹回合法范围再用
  const instrumental = Boolean(state.settings.instrumental);
  const vocalLanguage = state.settings.vocalLanguage === "en" ? "en" : "zh";
  const ok = window.confirm(
    "ACE-Step 1.5 能力边界确认：\n"
    + "· 支持带人声（中英文）完整乐曲或纯器乐，时长 10-480 秒，输出 48kHz wav；\n"
    + "· 按 6GB 显存档位设计（LM/DiT 走 CPU 卸载），峰值显存为预期值、尚未实测，验收以任务元数据 peak_vram_mb 为准；\n"
    + "· 不支持超长多轨道复杂编曲（无分轨导出/多轨工程）。\n\n"
    + `本次任务：BPM ${state.settings.bpm} · 目标 ${state.settings.musicDuration}s · `
    + `${instrumental ? "纯器乐" : `人声（${vocalLanguage}）`} · 与图片/视频共用串行队列（不并发抢占显存）。\n继续提交？`
  );
  if (!ok) return;
  state.submitting = true;
  els.imagineBtn.disabled = true;
  try {
    const session = ensureSession(text);
    const params = {
      bpm: state.settings.bpm,
      duration: state.settings.musicDuration,
      vocal_language: vocalLanguage,
      instrumental, // 带人声时歌词由 LM 依风格描述自动创作；纯器乐走 instrumental
      seed: randSeed(),
    };
    const batch = { id: uid(), kind: "music", mode: state.mode, prompt: text, time: formatTime(new Date().toISOString()), refPath: "", params, taskIds: [] };
    session.batches.push(batch);
    try {
      const rec = await submitTask(text, params, "music", state.settings.mengine);
      batch.taskIds.push(rec.task_id);
      showToast("音乐任务已排队（串行队列）：首次生成需加载本地权重（约 9.4GB，LM/DiT 走 CPU 卸载，约 1-3 分钟），产出 48kHz wav。", "success");
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

/* 「AI 智能体」板块排队的任务补挂到当前会话。智能体走后端 /api/agent/command，
 * 不经过上面的提交流程；不补这一步，智能体生成的图片/视频/音乐不会出现在会话流里，
 * 用户只会在提示里看到「已排入队列」却找不到产物。 */
async function attachAgentTask(task, meta) {
  if (!task || !task.task_id) return null;
  const info = meta || {};
  const session = ensureSession(info.prompt || "AI 智能体");
  if (!session) return null;
  const batch = {
    id: uid(),
    kind: info.kind || "imagine",
    mode: info.mode || "t2i",
    prompt: info.prompt || "",
    time: formatTime(new Date().toISOString()),
    refPath: info.refPath || "",
    params: info.params || {},
    taskIds: [],
  };
  session.batches.push(batch);
  try {
    const rec = await api(`/api/tasks/${encodeURIComponent(task.task_id)}`);
    batch.taskIds.push(rec.task_id);
    // 立刻写进本地缓存，卡片当场出现「排队中」，不用等下一轮轮询。
    state.tasks.set(rec.task_id, rec);
  } catch (error) {
    showToast(`智能体任务已排队，但读取任务状态失败：${error.message}`, "error");
  }
  persist();
  renderSessions();
  renderFeed(true);
  els.feed.scrollTop = els.feed.scrollHeight;
  return batch;
}
window.ChenGuAttachTask = attachAgentTask;

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
  /* U 不再是超分模型，而是把原图当参考图交给 Wan VACE 按更大画布重绘补细节。
     尺寸按 16 的倍数放 1.34 倍，并夹回 Wan 的 480p 档像素预算（VIDEO_MAX_PIXELS）。 */
  const w0 = Number(p.width) || 832, h0 = Number(p.height) || 480;
  let w = clampInt(w0 * 1.34 / 16, 16, 4096) * 16, h = clampInt(h0 * 1.34 / 16, 16, 4096) * 16;
  if (w * h > VIDEO_MAX_PIXELS) {
    const f = Math.sqrt(VIDEO_MAX_PIXELS / (w * h));
    w = clampInt(w * f / 16, 256, 4096) * 16;
    h = clampInt(h * f / 16, 256, 4096) * 16;
  }
  if (w <= w0 && h <= h0) {
    showToast("原图已在 480p 档像素预算上限附近，Wan 无法再放大（要更大尺寸需要超分或更大的模型）", "error");
    return;
  }
  const params = {
    width: w, height: h, steps: Number(p.steps) || 28,
    init_image_path: src.file.path,
    seed: Number.isInteger(p.seed) ? p.seed : randSeed(),
  };
  if (p.negative_prompt) params.negative_prompt = p.negative_prompt;
  const session = ensureSession(src.rec.prompt);
  const batch = { id: uid(), kind: "upscale", mode: "i2i", prompt: src.rec.prompt, time: formatTime(new Date().toISOString()), params, taskIds: [] };
  session.batches.push(batch);
  try {
    const rec = await submitTask(src.rec.prompt, params, "image", state.settings.vengine);
    batch.taskIds.push(rec.task_id);
    state.tasks.set(rec.task_id, rec);
    persist(); renderFeed(true);
    showToast(`放大任务已排队：Wan VACE 参考重绘补细节（${w}×${h}，480p 档像素预算内）`, "success");
  } catch (error) { showToast(`放大提交失败：${error.message}`, "error"); }
}

async function variation(taskId) {
  const src = upscaleSourceRecord(taskId); // 与 U 相同的取源逻辑
  if (!src) return;
  const p = src.rec.parameters || {};
  const session = ensureSession(src.rec.prompt);
  const batch = { id: uid(), kind: "variation", mode: "i2i", prompt: src.rec.prompt, time: formatTime(new Date().toISOString()), params: p, taskIds: [] };
  session.batches.push(batch);
  try {
    // 串行队列 + 6GB 档：一次只排 1 张 VACE 参考重绘变体（旧 SD 时代的 4 连发会堵死队列）
    const params = {
      width: Number(p.width) || 832, height: Number(p.height) || 480,
      steps: Number(p.steps) || 28,
      init_image_path: src.file.path,
      seed: randSeed(),
    };
    if (p.negative_prompt) params.negative_prompt = p.negative_prompt;
    batch.params = params;
    const rec = await submitTask(src.rec.prompt, params, "image", state.settings.vengine);
    batch.taskIds.push(rec.task_id);
    state.tasks.set(rec.task_id, rec);
    persist();
    renderFeed(true);
    showToast("变体任务已排队（Wan VACE 参考重绘，1 张）", "success");
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
    const fbMode = srcRec.task_type === "music"
      ? "music"
      : srcRec.task_type === "video"
        ? (srcRec.parameters && srcRec.parameters.init_image_path ? "i2v" : "t2v")
        : (srcRec.parameters && srcRec.parameters.init_image_path ? "i2i" : "t2i");
    const fbKind = srcRec.task_type === "music" ? "music" : srcRec.task_type === "video" ? "video" : "imagine";
    session.batches.push({ id: uid(), kind: fbKind, mode: fbMode, prompt: srcRec.prompt || "重试", time: formatTime(new Date().toISOString()), params: srcRec.parameters || {}, taskIds: [newRec.task_id] });
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
    if (event.key === "Enter" && !event.isComposing) commit(true);
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
  input.placeholder = "参考图的本机完整路径（绝对路径、具体图片文件），如 D:\\ChenguAI\\uploads\\photo.png";
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
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); els.prompt.focus(); } });
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
    if (!tab || !tab.dataset.mode) return;
    // 「AI 智能体」是前端视图切换（后端把命令路由进同一条队列），不是第六种业务模式，
    // 所以它不参与 setMode，只把 state.mode 借位成 "agent" 让 Tab 高亮正确。
    if (tab.dataset.mode === "agent" && typeof window.ChenGuAgent !== "undefined") {
      document.documentElement.dataset.mode = "agent";  // ChatGPT 风皮肤（规则 5）
      window.ChenGuAgent.open();
      return;
    }
    if (typeof window.ChenGuAgent !== "undefined") window.ChenGuAgent.close();
    setMode(tab.dataset.mode);
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

  /* 尺寸（图片 / 视频共用一组控件，绑到当前模式参数桶；480p 档按 16 的倍数） */
  const bindSize = (rangeId, numId, key, sync, lo, hi, step) => {
    $(rangeId).addEventListener("input", () => { state.p[key] = Number($(rangeId).value); sync(); persist(); });
    $(numId).addEventListener("change", () => {
      const v = clampInt(Number($(numId).value) || lo, lo, hi);
      state.p[key] = Math.round(v / step) * step;
      sync(); persist();
    });
  };
  $("aspect-chips-v").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;
    state.p.width = Number(chip.dataset.w);
    state.p.height = Number(chip.dataset.h);
    syncVideoSize(); persist();
  });
  bindSize("vwidth-range", "vwidth-num", "width", syncVideoSize, 256, 832, 16);
  bindSize("vheight-range", "vheight-num", "height", syncVideoSize, 256, 832, 16);

  /* 视频帧数 / 音乐参数（BPM、目标时长、纯器乐、人声语言） */
  $("frames-range").addEventListener("input", () => { state.p.frames = snapFrames(Number($("frames-range").value)); syncFrames(); persist(); });
  $("bpm-range").addEventListener("input", () => { state.settings.bpm = Number($("bpm-range").value); syncMusic(); updateComposerMeta(); persist(); });
  $("music-dur-range").addEventListener("input", () => { state.settings.musicDuration = Number($("music-dur-range").value); syncMusic(); updateComposerMeta(); persist(); });
  $("instrumental-toggle").addEventListener("click", () => {
    state.settings.instrumental = !state.settings.instrumental;
    syncMusic(); updateComposerMeta(); persist();
  });
  $("vocal-lang-select").addEventListener("change", () => {
    state.settings.vocalLanguage = $("vocal-lang-select").value === "en" ? "en" : "zh";
    updateComposerMeta(); persist();
  });

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

  /* 引擎选择 / 重启（按当前模态存 vengine / mengine，与桌面端 param_panel 同名） */
  els.engineSelect.addEventListener("change", () => {
    const key = MODALITY[state.mode] === "music" ? "mengine" : "vengine";
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

  /* 产物保存：自动另存目录（存到服务端 config.json，桌面端与网页端共用同一份） */
  $("autosave-save").addEventListener("click", saveSaveSettings);
  $("autosave-dir").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); saveSaveSettings(); }
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
      else if (act === "save") await saveTaskArtifact(task);
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
  $("lb-save").addEventListener("click", async (event) => {
    /* 支持「另存为」对话框的浏览器（Edge/Chrome）让用户自己挑位置；
       不支持的保持 <a download> 的默认行为（进浏览器下载目录）。 */
    if (typeof window.showSaveFilePicker !== "function") {
      showToast("已触发下载（浏览器下载管理里查看；开启「每次询问保存位置」可自选目录）", "success");
      return;
    }
    const lb = state.lightbox;
    const rec = lb ? state.tasks.get(lb.ids[lb.idx]) : null;
    const file = rec ? mainArtifact(rec) : null;
    if (!file) { showToast("该结果没有可另存的产物", "error"); return; }
    event.preventDefault();
    await saveArtifactToDisk(file);
  });
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
  state.mode = MODES.includes(state.settings.mode) ? state.settings.mode : "agent";
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
  loadSaveSettings();
  setInterval(() => {
    if (document.hidden) return;
    refreshHealth();
    refreshEngines();
    refreshTasks();
  }, 2500);
}

init();
