"use strict";

/* 辰鹄AI创作中心 前端 —— Midjourney Web 式交互，全部走现有 /api/* 与 /outputs/*。
 * 零外部依赖、离线可用；会话与参数保存在浏览器 localStorage，任务本体仍由后端 SQLite 队列管理。
 * 图生视频（i2v）走现有视频参数（init_image_path 作首帧），后端未改动。
 * 文生图 = Sana-1.6B 多语言版（sana-multiling，独立 .venv-image 与 models/sana-multiling）；
 * 图生图 = Wan2.1 VACE 单帧档（num_frames=1），与视频共用 .venv-video 与权重；
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
   文生图 sana-multiling（Sana-1.6B 多语言版）走独立的 .venv-image 与 models/sana-multiling；
   图生图 wan-i2i（VACE-1.3B 参考重绘）仍是 Wan2.1 的单帧档，与视频共用同一套权重与 venv。 */
const ENGINE_TASK_TYPES = {
  "sana-multiling": ["image"], "wan-i2i": ["image"],
  "wan-t2v": ["video"], "wan-i2v": ["video"],
  "ace-step-15": ["music"],
};
/* 模式 → 引擎白名单：一个模式只认一个引擎，避免选错权重。 */
const MODE_ENGINE_ALLOW = {
  t2i: ["sana-multiling"], i2i: ["wan-i2i"],
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
/* 视频真实像素预算 + token 档位账：与 app/engine/video/budget.py 同名同值，
   改一处必须同步另一处（1.3B 档最高就是约 480p，459264px = 832*480*115/100，整数算法 ≈ 0.46MP；
   注意别用 1.15 这种浮点写法：Python 侧 int(832*480*1.15) 会得到 459263，少 1 像素。
   桌面端 param_panel 也是 import 这同一个数，全仓只此一份）。
   6GB 显存档位真正的驱动量是 DiT 的 token 数 =（宽/16）×（高/16）×压缩帧数，
   压缩帧数 = (帧数-1)/4+1，上限 20000。
   **引擎不再自动降档**：超预算的组合在提交前就被前端挡下（后端同样会 400 兜底），
   提示里给出这一档的真实上限，让用户自己选方向。图生图走同一引擎的单帧档
   （num_frames=1），像素预算与显存门槛与视频一致，token 天然在线内。 */
const VIDEO_MAX_PIXELS = 459264;
const VIDEO_MAX_TOKENS = 20000;
const VIDEO_MIN_FRAMES = 5;
const VIDEO_MAX_FRAMES = 161;
/* 图生图 + 视频（Wan 480p 档）宽/高控件范围：倍数 16、滑杆 256-1024，与桌面端 param_panel 同口径。 */
const VIDEO_MULTIPLE = 16;
const VIDEO_MIN_SIDE = 256;
const VIDEO_MAX_SIDE = 1024;

/* ↓ 以下四个函数是 budget.py 里同名函数的直译（同一套数字、同一个算法），
      这样网页提交前算出的 token 与最长帧数，跟引擎子进程真跑时用的是同一份账。 */
const videoRoundMultiple = (value) => {
  const number = Math.max(VIDEO_MULTIPLE * 8, Math.min(Math.round(Number(value) || 0), 2048));
  return Math.round(number / VIDEO_MULTIPLE) * VIDEO_MULTIPLE;
};
const videoLatentFrames = (frames) => Math.floor((frames - 1) / 4) + 1;
const videoTokens = (width, height, frames) => Math.max(
  1,
  Math.floor(width / VIDEO_MULTIPLE) * Math.floor(height / VIDEO_MULTIPLE) * videoLatentFrames(frames),
);
/* 这一档分辨率在 token 预算内最长能跑到第几帧（4n+1；放不下则 0） */
const videoMaxFrames = (width, height) => {
  const cells = Math.max(1, Math.floor(width / VIDEO_MULTIPLE) * Math.floor(height / VIDEO_MULTIPLE));
  const latent = Math.floor(VIDEO_MAX_TOKENS / cells);
  if (latent < 1) return 0;
  return Math.min((latent - 1) * 4 + 1, VIDEO_MAX_FRAMES);
};
/* 保持当前比例，token 预算内允许的最大档位（格子 = 16px 一边） */
function videoMaxSize(frames, width, height) {
  const latent = videoLatentFrames(snapFrames(frames));
  const cellsBudget = Math.max(1, Math.floor(VIDEO_MAX_TOKENS / latent));
  let wc = Math.max(1, videoRoundMultiple(width) / VIDEO_MULTIPLE);
  let hc = Math.max(1, videoRoundMultiple(height) / VIDEO_MULTIPLE);
  if (wc * hc <= cellsBudget) return [wc * VIDEO_MULTIPLE, hc * VIDEO_MULTIPLE];
  const ratio = hc / wc;
  wc = Math.max(1, Math.floor(Math.sqrt(cellsBudget / ratio)));
  hc = Math.max(1, Math.round(wc * ratio));
  while (wc * hc > cellsBudget && Math.min(wc, hc) > 1) {
    if (wc >= hc) wc -= 1; else hc -= 1;
  }
  while (Math.round(wc + 1) * Math.max(1, Math.round((wc + 1) * ratio)) <= cellsBudget) {
    wc += 1;
    hc = Math.max(1, Math.round(wc * ratio));
  }
  return [wc * VIDEO_MULTIPLE, hc * VIDEO_MULTIPLE];
}
/* 按请求比例缩到 480p 像素预算内：只用来报「这一比例最大可用多少」，不改用户档位 */
function fitVideoPixels(width, height) {
  let w = videoRoundMultiple(width);
  let h = videoRoundMultiple(height);
  if (w * h > VIDEO_MAX_PIXELS) {
    const scale = Math.sqrt(VIDEO_MAX_PIXELS / (w * h));
    w = Math.max(VIDEO_MULTIPLE, videoRoundMultiple(w * scale));
    h = Math.max(VIDEO_MULTIPLE, videoRoundMultiple(h * scale));
    while (w * h > VIDEO_MAX_PIXELS) {
      if (w >= h) w = Math.max(VIDEO_MULTIPLE, w - VIDEO_MULTIPLE);
      else h = Math.max(VIDEO_MULTIPLE, h - VIDEO_MULTIPLE);
    }
  }
  return [w, h];
}
/* 把用户选的档位折算成判定 + 可显示的具体数字（对应 budget.evaluate） */
function budgetReport(width, height, frames) {
  const f = snapFrames(frames ?? 33);
  const pixels = width * height;
  const tokens = videoTokens(width, height, f);
  const capFrames = videoMaxFrames(width, height);
  const capSize = videoMaxSize(f, width, height);
  const overPixels = pixels > VIDEO_MAX_PIXELS;
  const overTokens = tokens > VIDEO_MAX_TOKENS;
  return {
    ok: !(overPixels || overTokens),
    width, height, frames: f,
    seconds: Number((f / VIDEO_FPS).toFixed(2)),
    pixels, tokens,
    capFrames,
    capSeconds: capFrames ? Number((capFrames / VIDEO_FPS).toFixed(2)) : 0,
    capSize, overPixels, overTokens,
  };
}
/* 超预算时的中文说明：具体数字 + 两条出路，不承诺任何自动降档（对应 budget.explain_over） */
function budgetProblemText(report, label = "视频") {
  if (report.overPixels) {
    const cap = fitVideoPixels(report.width, report.height);
    return `${label} ${report.width}×${report.height} 超出 Wan2.1 1.3B 的 480p 像素上限 ${VIDEO_MAX_PIXELS}px`
      + `（约 0.46MP）：本工具不会自动降档。这一比例最大可用 ${cap[0]}×${cap[1]}，请改到该尺寸或更小再提交。`;
  }
  const keepSize = report.capFrames >= VIDEO_MIN_FRAMES
    ? `保持 ${report.width}×${report.height}：最长 ${report.capFrames} 帧（${report.capSeconds} 秒 @${VIDEO_FPS}fps）。`
    : `保持 ${report.width}×${report.height}：连最低 ${VIDEO_MIN_FRAMES} 帧都放不下，只能降分辨率。`;
  return [
    `${label} ${report.width}×${report.height} @ ${report.frames} 帧需要 ${report.tokens} tokens，`
      + `超过 6GB 显存档位的 ${VIDEO_MAX_TOKENS} tokens 上限（这就是 6GB 峰值的驱动量）。`,
    keepSize,
    `保持 ${report.frames} 帧（${report.seconds} 秒）：最大只能 ${report.capSize[0]}×${report.capSize[1]}。`,
    "不会自动降档，也不会偷改帧数：请二选一后重新提交。",
  ].join(" ");
}
/* 文生图（sana-multiling，口径见 app/engine/image/plan.py 的 SANA_*）：
   对齐倍数 32（DC-AE 是 32 倍下采样）；宽/高滑杆 320-2048、步进 32；
   单遍像素预算 1100000（≈1024 级，超过就按预算出底图再 Lanczos 放大到目标尺寸）；
   目标像素硬上限 4200000（2048×2048，再高就是纯插值，引擎直接拒绝）。
   与桌面端 param_panel 的 IMAGE_T2I_MULTIPLE / IMAGE_T2I_MAX_PIXELS / IMAGE_T2I_MAX_TARGET_PIXELS 同一份数。 */
const IMAGE_T2I_MULTIPLE = 32;
const IMAGE_T2I_MIN_SIDE = 320;
const IMAGE_T2I_MAX_SIDE = 2048;
const IMAGE_T2I_MAX_PIXELS = 1100000;
const IMAGE_T2I_MAX_TARGET_PIXELS = 4200000;
/* 尺寸档位：与桌面端 param_panel.ASPECT_CHIPS_T2I / ASPECT_CHIPS_VIDEO 同名、同序、标签逐字一致。
   文生图（Sana）是 1024 级表，1920×1088 那条超出单遍预算、走「底图 + 放大」，标签里已写明；
   图生图与视频仍是原来的 480p 表。 */
const ASPECT_CHIPS_T2I = [
  [1024, 1024, "1:1 1024"],
  [1152, 640, "16:9 1024级"],
  [640, 1152, "9:16 1024级"],
  [1152, 864, "4:3 1024级"],
  [864, 1152, "3:4 1024级"],
  [1920, 1088, "16:9 1080p（放大）"],
];
const ASPECT_CHIPS_VIDEO = [
  [832, 480, "16:9"],
  [480, 832, "9:16"],
  [624, 624, "1:1"],
  [640, 480, "4:3"],
  [480, 640, "3:4"],
];
const aspectChipsFor = (mode) => (mode === "t2i" ? ASPECT_CHIPS_T2I : ASPECT_CHIPS_VIDEO);
/* 板块 → 尺寸口径：文生图走 Sana 的 32 倍数与 1.10MP 单遍预算；
   图生图 / 视频走 Wan 的 16 倍数与 480p 预算（预算与硬上限在 Wan 档是同一个数）。 */
const sizeLimits = (mode) => (mode === "t2i"
  ? {
    lo: IMAGE_T2I_MIN_SIDE, hi: IMAGE_T2I_MAX_SIDE, step: IMAGE_T2I_MULTIPLE,
    budget: IMAGE_T2I_MAX_PIXELS, target: IMAGE_T2I_MAX_TARGET_PIXELS,
  }
  : {
    lo: VIDEO_MIN_SIDE, hi: VIDEO_MAX_SIDE, step: VIDEO_MULTIPLE,
    budget: VIDEO_MAX_PIXELS, target: VIDEO_MAX_PIXELS,
  });
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
  t2i: "描述你想要的画面…（Sana-1.6B 多语言版 · 中文英文混着写都行 · 1024 级直出，可到 1080p）",
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
  /* 文生图（Sana）默认 1024×1024、steps 20（引擎按 10-40 夹取），落在它自己的单遍预算内；
     图生图与视频共用 Wan 单引擎，默认尺寸落在该引擎真实像素预算内（约 480p 档，
     见 app/engine/video/wan.py _MAX_PIXELS），steps 默认 28；音乐档不用尺寸，steps 用 turbo 默认 8。
     「AI 智能体」页不用参数面板，借用一个视频参数桶保持结构一致。 */
  const steps = mode === "music" ? 8 : mode === "t2i" ? 20 : 28;
  const width = mode === "t2i" ? 1024 : 832;
  const height = mode === "t2i" ? 1024 : 480;
  return { width, height, steps, frames: 33, seedLock: false, seed: null, negative: "", advanced: "" };
}
function defaultSettings() {
  const modes = {};
  for (const m of MODES) modes[m] = defaultModeParams(m);
  return {
    mode: "agent",
    refDir: "",
    vengine: "",                   // 图片 + 视频模态引擎（sana-multiling / wan-i2i / wan-t2v / wan-i2v，按标签页白名单自动落位）
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
  if (!file) { showToast("该任务还没有可另存的产物", "error"); return; }
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

/* 尺寸分区按板块换一套口径：文生图（Sana）用 1024 级档位表与 320-2048 / 步进 32，
   图生图与视频（Wan）用原来的 480p 档位表与 256-1024 / 步进 16。
   控件的 min/max/step 属性改了必须当场回填当前值，否则滑杆会停在被旧范围夹过的位置。 */
function applySizeChrome() {
  const lim = sizeLimits(state.mode);
  const title = $("size-title");
  if (title) {
    title.textContent = state.mode === "t2i" ? "生成尺寸（1024 级，可到 1080p）" : "生成尺寸（480p 档像素预算）";
  }
  const box = $("aspect-chips-v");
  const chips = aspectChipsFor(state.mode);
  const sig = chips.map((c) => c.join(":")).join("|");
  if (box.dataset.set !== sig) {
    box.dataset.set = sig;
    box.textContent = "";
    for (const [w, h, label] of chips) {
      const chip = el("button", "chip", label);
      chip.dataset.w = String(w);
      chip.dataset.h = String(h);
      box.appendChild(chip);
    }
  }
  for (const id of ["vwidth", "vheight"]) {
    for (const suffix of ["-range", "-num"]) {
      const input = $(id + suffix);
      input.setAttribute("min", String(lim.lo));
      input.setAttribute("max", String(lim.hi));
      input.setAttribute("step", String(lim.step));
    }
  }
}

function syncVideoSize() {
  const s = state.p;
  const lim = sizeLimits(state.mode);
  /* 桶里的值先按当前板块的倍数夹回来再回填：文生图的 1024 不会被 480p 档的旧上限卡回 832，
     反过来 480p 档的 624 进文生图也会落到 32 的倍数（640），不给引擎送一个不对齐的尺寸。 */
  const snapSide = (value) => {
    const raw = Number(value);
    const base = Number.isFinite(raw) && raw > 0 ? raw : lim.lo;
    return clampInt(Math.round(base / lim.step) * lim.step, lim.lo, lim.hi);
  };
  s.width = snapSide(s.width);
  s.height = snapSide(s.height);
  applySizeChrome();
  $("vwidth-range").value = s.width;
  $("vheight-range").value = s.height;
  $("vwidth-num").value = s.width;
  $("vheight-num").value = s.height;
  for (const chip of $("aspect-chips-v").children) {
    chip.classList.toggle("on", Number(chip.dataset.w) === s.width && Number(chip.dataset.h) === s.height);
  }
  const px = s.width * s.height;
  const f = snapFrames(s.frames ?? 33);
  const hint = $("v-vram-hint");
  const isImage = MODALITY[state.mode] === "image";
  if (state.mode === "t2i") {
    /* 文生图（Sana）：超过单遍预算不是错误，而是换「预算内出底图 + 放大」这条路径；
       超过目标硬上限才是真拒绝（滑杆范围到不了，走高级参数越界时提示出来）。 */
    if (px > IMAGE_T2I_MAX_TARGET_PIXELS) {
      hint.className = "hint warn";
      hint.textContent = `${s.width}×${s.height}（${(px / 1e6).toFixed(2)}MP）超过 Sana 的目标像素硬上限 ${IMAGE_T2I_MAX_TARGET_PIXELS}px（2048×2048）：再高就是纯插值，引擎直接拒绝，请把宽高调小。`;
    } else if (px > IMAGE_T2I_MAX_PIXELS) {
      hint.className = "hint warn";
      hint.textContent = `${s.width}×${s.height}（${(px / 1e6).toFixed(2)}MP）超过 Sana 的 1024 级单遍像素预算 ${IMAGE_T2I_MAX_PIXELS}px：引擎会按预算出底图再放大到目标尺寸（产物元数据 upscaled=true）。`;
    } else {
      hint.className = "hint";
      hint.textContent = `文生图像素 ${(px / 1e6).toFixed(2)}MP · Sana-1.6B 多语言版按 32 的倍数对齐（DC-AE 32 倍下采样），按 6GB 显存档位设计（fp16 三件权重走逐模块 CPU 换入，峰值为按体积推算的预期值、尚未实测，验收看任务元数据 peak_vram_mb）；加载权重需要约 16GB 空闲物理内存。`;
    }
  } else if (px > lim.budget || (MODALITY[state.mode] === "video" && !budgetReport(s.width, s.height, f).ok)) {
    hint.className = "hint warn";
    hint.textContent = budgetProblemText(
      budgetReport(s.width, s.height, isImage ? 1 : f),
      isImage ? "图生图" : "视频",
    );
  } else if (isImage) {
    hint.className = "hint";
    hint.textContent = `图生图像素 ${(px / 1e6).toFixed(2)}MP · 图生图 wan-i2i 与视频同一个 Wan 引擎的单帧档（num_frames=1），按 6GB 显存档位设计（bf16 + 逐层 CPU 卸载；单帧 token 约为 81 帧视频的 1/13，所以图生图峰值预期低于视频实测的约 2GB，未实测）；逐层卸载还需要约 16GB 空闲物理内存。要 1024 级的高像素出图请用「文生图」板块。`;
  } else {
    const rep = budgetReport(s.width, s.height, f);
    hint.className = "hint";
    hint.textContent = `视频档位 ${s.width}×${s.height} @ ${f} 帧（${rep.seconds} 秒 @16fps）`
      + ` = ${rep.tokens} tokens / 上限 ${VIDEO_MAX_TOKENS}；这一比例最长 ${rep.capFrames} 帧（${rep.capSeconds} 秒），`
      + `这个帧数下最大 ${rep.capSize[0]}×${rep.capSize[1]}。档位钉死：引擎按你选的档位原样出片，不会自动降档；`
      + `越线在提交前就挡下，不会白排队。逐层 CPU 卸载还需要约 16GB 空闲物理内存。`;
  }
  updateComposerMeta();
}

function syncFrames() {
  const f = snapFrames(state.p.frames ?? 33);
  state.p.frames = f;
  $("frames-range").value = f;
  $("frames-label").textContent = `${(f / VIDEO_FPS).toFixed(2)} 秒 · ${f} 帧 @16fps`;
  syncVideoSize();   // 时长一动，档位 token 与「这一比例最长几帧」就按同一份账重算一遍
  updateComposerMeta();
}

function syncSteps() {
  const range = $("steps-range");
  /* 文生图（Sana）引擎按 10-40 夹取，滑杆就给 10-40（默认 20）；其余板块保持原来的 1-50。
     切板块后范围变了，先把桶里的值夹进新范围再回填，不然显示值与提交值会不一致。 */
  const lo = state.mode === "t2i" ? 10 : 1;
  const hi = state.mode === "t2i" ? 40 : 50;
  state.p.steps = clampInt(state.p.steps, lo, hi);
  range.min = String(lo);
  range.max = String(hi);
  range.value = state.p.steps;
  $("steps-label").textContent = `${state.p.steps} 步`;
  const modality = MODALITY[state.mode];
  if (modality === "music") {
    $("steps-hint").textContent = "音乐 turbo 权重默认 8 步（推荐）；最多 50 步，步数越多等待越长。";
  } else if (state.mode === "t2i") {
    $("steps-hint").textContent = "文生图（Sana）默认 20 步，引擎范围 10-40；6GB 上步数直接决定等待时长。";
  } else if (modality === "image") {
    $("steps-hint").textContent = "图生图推荐 28 步（默认）；6GB 上步数直接决定等待时长。";
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
  if (!state.settings.modes[mode]) state.settings.modes[mode] = defaultModeParams(mode);
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

/* 图片档参数：文生图走 sana-multiling（Sana 按 32 的倍数、steps 由引擎夹到 10-40），
   图生图走 wan-i2i 单帧档（引擎固定 num_frames=1）；两者都不传 num_frames
   （worker 的图片白名单也不收），i2i 的参考图同样走 init_image_path。 */
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

/* AI 智能体板块不绑定某一个引擎：后端按板块自动挑引擎（文生图 sana-multiling / 图生图 wan-i2i、
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
    } else if (state.mode === "t2i") {
      /* 文生图换型后走独立环境：.venv-image + models\sana-multiling，与视频那套完全分开。 */
      els.engineSelect.appendChild(el("option", null, "文生图引擎未注册 · 需 .venv-image 与 models\\sana-multiling"));
      els.engineState.textContent = "文生图引擎未注册：需 .venv-image（torch cu130 + diffusers + transformers，可用 scripts\\verify_env.py 自检）与 models\\sana-multiling 权重（运行 scripts\\download_sana_weights.py，Apache-2.0），未就绪前不会提交、不伪造输出";
    } else if (modality === "image") {
      els.engineSelect.appendChild(el("option", null, "图生图引擎未注册 · 需 .venv-video 与 models\\wan2.1"));
      els.engineState.textContent = "图生图引擎未注册：图生图与视频共用 .venv-video（运行 scripts\\setup_video_venv.ps1）与 models\\wan2.1 权重（运行 scripts\\download_video_weights.py，Apache-2.0），未就绪前不会提交、不伪造输出";
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
  if (hint) hint.textContent = engineModeHint(state.mode);
  els.engineState.textContent = current ? (current.message || label) : label;
}

/* 与桌面端 app/ui/widgets/param_panel.py ENGINE_MODE_HINTS 同一份口径，按板块分四段
   （t2i / i2i / video / music）：文生图（sana-multiling，Sana-1.6B 多语言版）与
   图生图（wan-i2i，VACE 参考重绘）现在是两个不同的引擎、两个运行环境，必须分开讲，
   不能再用一段「图片」混着说。
   视频档的 2021MB 是真机实测；文生图 / 图生图 / 音乐档的 6GB 峰值都是设计预期，尚未实测
   （验收看任务元数据里的 peak_vram_mb），文案里必须写清楚，不许冒充实测。
   引擎说明将来进 i18n：词典的 key 就是界面上那句中文，所以下面 t2i / i2i 两段一段就是一整句，
   不做多段拼接，也不插动态片段，否则两端逐字一致和翻译都对不上。 */
function modeHintKey(mode) {
  /* 板块 → 引擎说明文案的键（与桌面端 param_panel.mode_hint_key 同一份口径）。 */
  if (mode === "t2i") return "t2i";
  if (mode === "i2i") return "i2i";
  if (mode === "music") return "music";
  return "video";
}
function engineModeHint(mode) {
  const key = modeHintKey(mode);
  if (key === "music") {
    return "ACE-Step 1.5（MIT，本地离线，无云端 API）：48kHz wav，带人声（中英文）或纯器乐，10-480 秒。"
      + "按 6GB 显存档位设计：5Hz 语言模型与 DiT 走 CPU 卸载，峰值显存为预期值（未实测，验收看任务元数据 peak_vram_mb）；"
      + "turbo 默认 8 步。权重在 models\\ace-step-1.5（约 9.4GB，占物理内存），首次加载 1-3 分钟。"
      + "与图片/视频共用 FIFO 串行队列。"
      /* 下面的 6144 就是本文件上面的 MUSIC_FREE_FLOOR_MB，与桌面端 param_panel 逐字一致；
         改门槛数字要同时改两处，别只改一处。 */
      + "提交前本端会先把自己拉起的空闲引擎释放掉再实测空闲显存，空闲低于 6144MB 会拦下提交并点名占用者。";
  }
  if (key === "t2i") {
    /* 规格 2.1 逐字；源码里每处路径写两个反斜杠，界面上显示出来是一个。 */
    return "Sana-1.6B 多语言版（Apache-2.0；文本编码器是 Gemma-2-2B-IT，受 Gemma Terms of Use 约束；本地离线推理，无云端 API）：文生图引擎 sana-multiling，输出 PNG。中文、英文、中英混合与 Emoji 提示词原生支持（模型卡原文声明 it supports Emoji, Chinese and English and all mixed prompts），不做任何翻译改写，提示词原样进编码器。按 6GB 显存档位设计：fp16 三件权重（编码器 4.99GB / Linear-DiT 3.06GB / DC-AE VAE 1.19GB）走逐模块 CPU 换入，任一时刻显存里只有当前那一件，峰值按最大单件加激活压在 6GB 内——这是按权重体积推算的预期值，尚未真机实测，验收看任务元数据 peak_vram_mb；空闲显存低于约 5GB 会自动退到逐层换入（峰值更低、慢两三倍），低于约 1500MB 会点名占用者并明确拒绝，不会硬扛 OOM。加载权重需要约 16GB 空闲物理内存，不足会在加载前拦截。分辨率必须按 32 的倍数（DC-AE 32 倍下采样），单遍上限约 1.10MP（1024 级直出）；超过单遍预算走「预算内出底图 + Lanczos 放大」，1080p 就是这条路径，元数据里如实标 upscaled=true、tiles=0，不谎称高清重绘。CFG 固定 4.5、不是用户参数，所以界面上没有 CFG 滑杆。权重在 models\\sana-multiling（约 9.3GB，只含 fp16 变体），缺失时用 scripts\\download_sana_weights.py 补。出图前先释放本机 LLM 引擎占的显存（scripts\\release_local_engine.bat）。与视频/音乐共用 FIFO 串行队列，一次 1 张。";
  }
  if (key === "i2i") {
    /* 规格 2.2 逐字；同上，路径在源码里是两个反斜杠。 */
    return "Wan2.1-VACE-1.3B 参考重绘（Apache-2.0，本地离线推理，无云端 API）：图生图引擎 wan-i2i，必须上传参考图，输出 PNG。文本编码器 UMT5-XXL 原生支持中英文提示词。与视频引擎共用 .venv-video 与 models\\wan2.1 权重：bf16 + 逐层 CPU 卸载 + VAE tiling，视频档真机实测峰值 2021MB；单帧的 token 约为 81 帧视频的 1/13，图生图档峰值预期低于该实测值（尚未实测，验收看任务元数据 peak_vram_mb）；逐层卸载需要约 16GB 空闲物理内存。分辨率停在 480p 档（宽高 16 的倍数，约 0.46MP 像素预算）；超预算「不会自动降档」，提交前会被挡下并给出这一比例的最大可用档位。要 1024 级的高像素出图请用「文生图」板块（Sana 多语言版）。";
  }
  return "Wan2.1 1.3B（Apache-2.0，本地离线）：文生视频 wan-t2v / 图生视频 wan-i2v（VACE），导出 16fps mp4，帧数 5-161（自动取 4n+1）。"
    + "分辨率停在 480p 档（宽高 16 的倍数，约 0.46MP 像素预算），1.3B 档最高约 480p，要 1080p 得另配超分或更大的模型。"
    + "按 6GB 显存档位设计：bf16 + 逐层 CPU 卸载，真机实测峰值 2021MB，起跑只需约 1500MB 空闲显存；逐层卸载还需要约 16GB 空闲物理内存。"
    + "档位钉死：显存上限由 DiT token 数决定（宽/16 × 高/16 × 压缩帧数，压缩帧数 =（帧数-1)/4+1，上限 20000），"
    + "超线的组合不会自动降分辨率也不会偷改帧数，而是在提交前就被挡下，提示里写清保持分辨率的最长帧数与保持帧数的最大分辨率"
    + "（面板上实时显示当前档位的这两个上限）。"
    + "与智能体排的任务共用 FIFO 串行队列。"
    /* 下面的 2000 就是本文件上面的 VIDEO_FREE_FLOOR_MB，与桌面端 param_panel 逐字一致；
       改门槛数字要同时改两处，别只改一处。 */
    + "提交前本端会先把自己拉起的空闲引擎与大模型释放掉再实测空闲显存，空闲低于 2000MB 会拦下提交并点名是哪个程序占着显存；"
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
  const vid = files.find((f) => isVideoUrl(f.url));
  const frame = files.find((f) => /\.(png|jpe?g|webp)$/i.test(f.url || ""));
  if (vid) {
    /* 有视频就只渲染视频：封面用引擎写出来的首帧当 poster。以前 mp4 和封面 png
       会一起贴进卡片，同一帧画两遍，还挤掉了视频本体。 */
    const v = el("video");
    v.controls = true; v.muted = true; v.loop = true; v.preload = "metadata";
    v.src = vid.url;
    if (frame) v.poster = frame.url;
    v.dataset.task = rec.task_id;
    tile.appendChild(v);
  } else if (frame) {
    const img = el("img", "frame");
    img.loading = "lazy"; img.src = frame.url; img.alt = "首帧";
    img.dataset.task = rec.task_id;
    tile.appendChild(img);
  }
  const p = rec.parameters || {};
  const frames = Number(p.num_frames) || 0;
  tile.appendChild(el("div", "tile-foot",
    `seed ${p.seed ?? "—"} · ${p.width ?? "?"}×${p.height ?? "?"} · ${frames ? `${(frames / VIDEO_FPS).toFixed(2)}s @16fps` : "时长?"}`));
  if (vid) {
    const a = el("a", "vu dl", "⬇下载视频");
    a.href = vid.url + (vid.url.includes("?") ? "&" : "?") + "download=1";
    a.download = vid.name || "辰鹄视频.mp4";
    const acts = el("div", "tile-acts");
    /* 「⛶」= 在灯箱里开大播放器（对应桌面端的「▶ 播放」窗）；双击视频同样是这个动作。
       纯图形按钮：不新增要翻译的文案，跟 ✕ / ‹ / 📁 这类图标按钮一致。 */
    const btnBig = el("button", "vu", "⛶");
    btnBig.dataset.act = "enlarge"; btnBig.dataset.task = rec.task_id;
    const btnSave = el("button", "vu"); btnSave.innerHTML = "<b>⬇</b>另存到硬盘";
    btnSave.dataset.act = "save"; btnSave.dataset.task = rec.task_id;
    acts.append(btnBig, a, btnSave);
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
    /* 文生图 = sana-multiling（独立 .venv-image 与 models\sana-multiling）；
       图生图与视频 = Wan2.1 那一套（.venv-video 与 models\wan2.1）。显存门槛两端同一档位。 */
    const want = modality;
    const label = modality === "video" ? "视频" : state.mode === "t2i" ? "文生图" : "图生图";
    const engineLabel = state.mode === "t2i" ? "Sana-1.6B 多语言版" : "Wan2.1";
    const engineFix = state.mode === "t2i"
      ? "需 .venv-image（torch cu130 + diffusers + transformers，可用 scripts\\verify_env.py 自检）与 models\\sana-multiling 权重（运行 scripts\\download_sana_weights.py，Apache-2.0）"
      : "需 .venv-video（运行 scripts\\setup_video_venv.ps1）与 models\\wan2.1 权重（运行 scripts\\download_video_weights.py，Apache-2.0）";
    if (!state.engines.some((e) => (ENGINE_TASK_TYPES[e.engine_id] || []).includes(want))) {
      showToast(`${engineLabel} ${label}引擎未注册：${engineFix}；未就绪前不会提交任务，也不会伪造输出。`, "error");
      return;
    }
    let vhw = null;
    try { vhw = await api("/api/hardware"); } catch { /* 探测失败不误判，交由后端 health_check 兜底 */ }
    if (vhw && !vhw.gpu) {
      showToast(`显存不足，无法启动 ${engineLabel} ${label}模型：检测不到 NVIDIA 显卡（最低要求 6GB 显存档位）。`, "error");
      return;
    }
    if (vhw && vhw.gpu && vhw.gpu.memory_total_mb < VIDEO_MIN_VRAM_MB) {
      showToast(`显存不足，无法启动 ${engineLabel} ${label}模型：显卡 ${vhw.gpu.name || "?"} 仅 ${vhw.gpu.memory_total_mb} MB，最低要求 ${VIDEO_MIN_VRAM_MB} MB（6GB 档）。`, "error");
      return;
    }
    const vFloor = await vramFloorCheck(VIDEO_FREE_FLOOR_MB);
    if (!vFloor.ok) {
      const gave = vFloor.released && vFloor.released.length
        ? `本端已经先把自己拉起的空闲引擎释放掉了（${vFloor.released.join("、")}）；`
        : "本端没有可释放的空闲引擎；";
      showToast(`显存不足，无法启动 ${engineLabel} ${label}模型：按进程实测还能用的空闲显存只有 ${vFloor.free} MB（6GB 档起跑需约 ${VIDEO_FREE_FLOOR_MB} MB 空闲）。${gave}显存目前主要被 ${vFloor.holders || "别的程序"} 占着——退出占显存的程序再提交。（这里不是按 nvidia-smi 的「已用」算的：Windows 上那个数把驱动保留的、以及已经结束还没回收的进程也算进去，明显偏大。）`, "error");
      return;
    }
  }
  if (!NEEDS_REF.has(state.mode) && state.ref) clearRef(); // 文生图/文生视频不挂垫图
  let extraParams = {};
  if (state.ref) {
    const path = state.ref.path.trim();
    if (!path) { showToast("参考图需要本机完整路径。填路径后再提交，或点 ✕ 取消引用。", "error"); return; }
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
      /* 提交前先把档位账算清：超线就不排队，直接把这一档的真实上限讲成具体数字，
         不让用户白试一次。图生视频的目标尺寸由首帧比例决定（浏览器读不到本机图片的
         尺寸），这一路交给后端按首帧真实尺寸判定（同一份 budget.py），越线返回 400
         并带同样的具体数字，也不会进队列。 */
      if (state.mode === "t2v") {
        const report = budgetReport(vparams.width, vparams.height, vparams.num_frames);
        if (!report.ok) {
          showToast(budgetProblemText(report), "error");
          return;   // finally 会恢复按钮，不写批次、不排队
        }
      }
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
    const base = collectImageParams(refPath); // 图片档一次 1 张：参数不带 num_frames / strength（文生图 Sana、图生图 Wan 单帧档）
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
      /* 排队提示按板块分：文生图是 Sana-1.6B 多语言版（规格 2.3），图生图仍是 Wan VACE 单帧档。
         按板块判断而不是按「有没有挂垫图」判断，图生图没挂上路径时也不会串成文生图的提示。 */
      showToast(state.mode === "i2i"
        ? "图生图已排队：Wan2.1-VACE 按垫图参考重绘（单帧 PNG，480p 档），首次生成需加载权重，通常数分钟"
        : "文生图已排队：Sana-1.6B 多语言版（PNG，1024 级直出，超预算按底图+放大），首次生成需加载权重，通常数分钟", "success");
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
  /* 「放大」按源图出自哪个引擎分两条真能跑的路，与桌面端 creation_page.upscale() 同口径：
     文生图（sana-multiling）不接受垫图 → 同提示词同 seed 按更大目标尺寸重新出图；超出
     IMAGE_T2I_MAX_PIXELS 单遍预算时引擎按预算出底图再 Lanczos 放大到目标尺寸
     （元数据 upscaled=true）。口径＝32 的倍数（DC-AE 32 倍下采样）、边长 320-2048、
     目标不超过 IMAGE_T2I_MAX_TARGET_PIXELS（2048 级那档）。
     图生图（wan-i2i）与 2026-11-10 已下架的老文生图记录 → 图生图参考重绘：16 的倍数、
     VIDEO_MAX_PIXELS（480p 档）预算，超了就等比缩回。
     引擎按源图点名，不取当前板块的 state.settings.vengine：在视频板块点图片卡片的
     「放大」会把图片任务派给 wan-t2v，或把垫图塞给 sana-multiling，两边都会被引擎拒绝。 */
  const fromSana = (src.rec.engine_id || "") === "sana-multiling";
  const engineId = fromSana ? "sana-multiling" : "wan-i2i";
  const mult = fromSana ? IMAGE_T2I_MULTIPLE : 16;
  const lo = fromSana ? IMAGE_T2I_MIN_SIDE : 256;
  const hi = fromSana ? IMAGE_T2I_MAX_SIDE : VIDEO_MAX_SIDE;
  const budget = fromSana ? IMAGE_T2I_MAX_TARGET_PIXELS : VIDEO_MAX_PIXELS;
  const w0 = Number(p.width) || (fromSana ? 1024 : 832), h0 = Number(p.height) || (fromSana ? 1024 : 480);
  const align = (v) => clampInt(v / mult, lo / mult, hi / mult) * mult;
  let w = align(w0 * 1.34), h = align(h0 * 1.34);
  if (w * h > budget) {
    const f = Math.sqrt(budget / (w * h));
    w = align(w * f); h = align(h * f);
  }
  if (w <= w0 && h <= h0) {
    showToast(fromSana
      ? `源图 ${w0}×${h0} 已在 Sana 的目标硬上限（约 ${Math.round(budget / 1000)}K 像素，2048 级）附近，Sana 档无法再放大；要更大尺寸得另配超分或更大模型`
      : `源图 ${w0}×${h0} 已在 480p 档像素预算（约 ${Math.round(budget / 1000)}K 像素）上限附近，Wan 档无法再放大；要更大尺寸得另配超分或更大模型`, "error");
    return;
  }
  const params = {
    width: w, height: h, steps: Number(p.steps) || (fromSana ? 20 : 28),
    seed: Number.isInteger(p.seed) ? p.seed : randSeed(),
  };
  if (!fromSana) params.init_image_path = src.file.path; // 只有 wan-i2i 吃垫图，Sana 拒绝输入图片
  if (p.negative_prompt) params.negative_prompt = p.negative_prompt;
  const session = ensureSession(src.rec.prompt);
  const batch = { id: uid(), kind: "upscale", mode: fromSana ? "t2i" : "i2i", prompt: src.rec.prompt, time: formatTime(new Date().toISOString()), params, taskIds: [] };
  session.batches.push(batch);
  try {
    const rec = await submitTask(src.rec.prompt, params, "image", engineId);
    batch.taskIds.push(rec.task_id);
    state.tasks.set(rec.task_id, rec);
    persist(); renderFeed(true);
    showToast(fromSana
      ? `放大任务已排队：Sana-1.6B 同提示词同 seed 按更大尺寸重新出图（${w}×${h}；按 32 的倍数、单边不超过 2048；超过 1024 级单遍预算 ${Math.round(IMAGE_T2I_MAX_PIXELS / 1000)}K 像素时引擎按预算出底图再放大到目标尺寸，元数据 upscaled=true）`
      : `放大任务已排队：Wan VACE 参考重绘补细节（${w}×${h}，480p 档像素预算内）`, "success");
  } catch (error) { showToast(`放大提交失败：${error.message}`, "error"); }
}

async function variation(taskId) {
  const src = upscaleSourceRecord(taskId); // 与 U 相同的取源逻辑
  if (!src) return;
  const p = src.rec.parameters || {};
  // 变体 = 换 seed 再走一遍：文生图（sana-multiling）源图按原尺寸重新出图（不接受垫图，
  // 换 seed 本身就是新构图）；图生图与 2026-11-10 已下架的老文生图记录拿源图当垫图走 wan-i2i 参考重绘。
  const fromSana = (src.rec.engine_id || "") === "sana-multiling";
  const engineId = fromSana ? "sana-multiling" : "wan-i2i";
  const session = ensureSession(src.rec.prompt);
  const batch = { id: uid(), kind: "variation", mode: fromSana ? "t2i" : "i2i", prompt: src.rec.prompt, time: formatTime(new Date().toISOString()), params: p, taskIds: [] };
  session.batches.push(batch);
  try {
    // 串行队列 + 6GB 档：一次只排 1 张（旧 SD 时代的 4 连发会堵死队列），无 strength 参数
    const params = {
      width: Number(p.width) || (fromSana ? 1024 : 832), height: Number(p.height) || (fromSana ? 1024 : 480),
      steps: Number(p.steps) || (fromSana ? 20 : 28),
      // seed 规则与桌面端逐字一致：源任务有整数 seed 就 +1（可复现的变体链），
      // 没有就发一个新随机 seed。绝不能沿用源 seed——那等于把同一张图重排一遍。
      seed: Number.isInteger(Number(p.seed)) ? Number(p.seed) + 1 : randSeed(),
    };
    if (!fromSana) params.init_image_path = src.file.path; // 只有 wan-i2i 吃垫图
    if (p.negative_prompt) params.negative_prompt = p.negative_prompt;
    batch.params = params;
    const rec = await submitTask(src.rec.prompt, params, "image", engineId);
    batch.taskIds.push(rec.task_id);
    state.tasks.set(rec.task_id, rec);
    persist();
    renderFeed(true);
    showToast(fromSana
      ? "变体任务已排队（Sana-1.6B 同提示词换 seed 重新出图，1 张）"
      : "变体任务已排队（Wan VACE 参考重绘，1 张）", "success");
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
  showToast("参考图已载入预览。确认/修正路径即可提交（目录已自动记住；浏览器无法读取剪贴板图片的真实磁盘路径）。", "success");
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
    : "垫图只作参考重绘的参照，改动幅度由提示词决定（图生图引擎没有「重绘强度」这个参数）");
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
  const file = mainArtifact(rec) || rec.output_files[0];
  const isVid = isVideoUrl(file.url);
  const img = $("lb-img");
  const movie = $("lb-video");
  /* 换页/换类型前先把上一段停掉：藏起来的 <video> 照样在出声，切到图片听着很怪。 */
  movie.pause();
  img.classList.toggle("hidden", isVid);
  movie.classList.toggle("hidden", !isVid);
  /* V 变化和 U 放大只对图片有意义：视频卡片上本来也没有这两个动作。 */
  $("lb-v").classList.toggle("hidden", isVid);
  $("lb-u").classList.toggle("hidden", isVid);
  if (isVid) {
    movie.src = file.url;
    const frame = (rec.output_files || []).find((f) => /\.(png|jpe?g|webp)$/i.test(f.url || ""));
    if (frame) movie.poster = frame.url;
    else movie.removeAttribute("poster");
    /* 点开卡片本身是用户手势，通常能直接播；被浏览器拦下就让用户按播放键。 */
    movie.play().catch(() => {});
  } else {
    img.src = file.url;
  }
  const save = $("lb-save");
  save.href = file.url;
  save.download = file.name || (isVid ? "辰鹄视频.mp4" : "辰鹄图片.png");
  const p = rec.parameters || {};
  const frames = Number(p.num_frames) || 0;
  const stamp = ` · ${formatTime(rec.finished_at)}`;
  const head = `${rec.prompt}\n${p.width ?? "?"}×${p.height ?? "?"}`;
  $("lb-meta").textContent = isVid
    ? `${head} · ${frames ? `${(frames / VIDEO_FPS).toFixed(2)}s @${VIDEO_FPS}fps` : "时长?"} · seed ${p.seed ?? "随机"}${p.init_image_path ? " · img2vid" : ""}${stamp}`
    : `${head} · steps ${p.steps ?? "?"} · seed ${p.seed ?? "随机"}${p.init_image_path ? " · img2img" : ""} · ${formatTime(rec.finished_at)}`;
  $("lb-meta").style.whiteSpace = "pre-wrap";
  $("lb-prev").classList.toggle("hidden", lb.ids.length < 2);
  $("lb-next").classList.toggle("hidden", lb.ids.length < 2);
}

function closeLightbox() {
  state.lightbox = null;
  const movie = $("lb-video");
  movie.pause();
  /* 关掉就把源卸掉：不这么做，解码器和音频会一直留着。 */
  movie.removeAttribute("src");
  movie.load();
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

  /* 尺寸（图片 / 视频共用一组控件，绑到当前模式参数桶）。范围与倍数按板块现取现用：
     文生图（Sana）320-2048 / 步进 32，图生图与视频（Wan）256-1024 / 步进 16，
     所以 lo/hi/step 不能写死在绑定参数里，只能在事件里读 sizeLimits(state.mode)。 */
  const bindSize = (rangeId, numId, key, sync) => {
    $(rangeId).addEventListener("input", () => { state.p[key] = Number($(rangeId).value); sync(); persist(); });
    $(numId).addEventListener("change", () => {
      const lim = sizeLimits(state.mode);
      const v = clampInt(Number($(numId).value) || lim.lo, lim.lo, lim.hi);
      state.p[key] = Math.round(v / lim.step) * lim.step;
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
  bindSize("vwidth-range", "vwidth-num", "width", syncVideoSize);
  bindSize("vheight-range", "vheight-num", "height", syncVideoSize);

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
      else if (act === "enlarge") {
        /* 「⛶」：把这条产物放进灯箱——图片是大图，视频是带控件的大播放器。 */
        const host = actionEl.closest("[data-batch]");
        if (host) openLightbox(host.dataset.batch, task);
      }
      return;
    }
    const img = event.target.closest("img[data-task]");
    if (img) {
      const tile = img.closest("[data-batch]");
      openLightbox(tile.dataset.batch, img.dataset.task);
    }
  });

  /* 卡片里的视频：单击归播放器自己的控件，双击才开大播放器（也走「⛶」）。 */
  els.feed.addEventListener("dblclick", (event) => {
    const movie = event.target.closest("video[data-task]");
    if (!movie) return;
    const tile = movie.closest("[data-batch]");
    if (tile) openLightbox(tile.dataset.batch, movie.dataset.task);
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
    if (!file) { showToast("该任务还没有可另存的产物", "error"); return; }
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
    /* 焦点在播放器上时，左右键归播放器用（快进/快退），不再翻上一张/下一张。 */
    else if (event.key === "ArrowLeft" && event.target !== $("lb-video")) lbShift(-1);
    else if (event.key === "ArrowRight" && event.target !== $("lb-video")) lbShift(1);
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
