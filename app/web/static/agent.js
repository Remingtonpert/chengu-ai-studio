/* 「AI 智能体」面板（第六板块，排在 Tab 第一位）。
 *
 * 用户在自然语言输入框里说话 → 后端 app/agent 判定动作与模态 →
 * 生成类命令排进与六个板块（AI智能体、文生图、图生图、文生视频、图生视频、AI音乐）
 * 完全相同的任务队列（同一个 SQLite FIFO、同一个执行者锁），
 * 聊天/文案/写文档走项目自带的大模型端点（127.0.0.1:11435），朗读走 Windows 系统语音，
 * 「做个表格 / 写份方案 / 做个 PPT」由本机写库落成 .docx/.xlsx/.pptx 并给出下载链接。
 *
 * 这里只负责界面：不复制任何调度逻辑，也不伪造结果；后端回报 error / degraded 就照实显示。
 * 面板插在 #mode-tabs 之后，用 main[data-view="agent"] 切换显隐，不动 app.js 的渲染主循环。
 */
(function () {
  "use strict";

  const ENDPOINTS = {
    command: "/api/agent/command",
    capabilities: "/api/agent/capabilities",
    uploads: "/api/uploads",
  };
  const MODE_TO_TAB = { t2i: "t2i", i2i: "i2i", t2v: "t2v", i2v: "i2v", music: "music" };
  const HISTORY_LIMIT = 12;
  // 控制台刚起来的头几秒，引擎还在后台一个个注册，给「AI 智能体」专配的大模型实例
  // 也可能还没就位。所以能力探测降级不当终局：自动再探几次，能力条上也留一个手动按钮。
  const PROBE_RETRIES = 8;  // 自带大模型首次加载权重要几十秒，自动重试的窗口得盖住这次冷启动
  const PROBE_RETRY_MS = 8000;
  const EXAMPLES = [
    "画一张赛博朋克雨夜街头，16:9",
    "生成一段10秒的海浪拍打礁石延时摄影",
    "生成一首轻快的钢琴曲，纯音乐，90秒",
    "把这张图改成水彩风",
    "帮我写一条小红书文案，介绍这款保温杯",
    "做个新品上市的排期表",
    "写份活动执行方案，做成 Word 文档",
    "做个产品介绍 PPT",
    "出图步数一般设多少？",
  ];

  let open = false;
  let previousMode = "t2i";
  let caps = null;
  let probing = false;
  let probeRetries = 0;
  let busy = false;
  let refImage = null;
  let history = [];
  // 首屏只留输入框和示例，长说明与显存读数都默认收起：
  // 「帮助」展开那段能力说明，「调试信息」才把显存/加载参数那类技术读数拼到最底下。
  let helpOpen = false;
  let debugOn = false;

  let panel = null;
  let logEl = null;
  let textEl = null;
  let sendBtn = null;
  let statusEl = null;
  let refEl = null;

  // ------------------------------------------------------------------ 工具

  function node(html) {
    const tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    return tpl.content.firstElementChild;
  }

  function notify(message, kind) {
    if (typeof window.showToast === "function") window.showToast(message, kind || "");
  }

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  // ------------------------------------------------------------------ 样式

  const STYLE = `
#agent-panel{display:none;flex-direction:column;gap:10px;min-height:0;flex:1 1 auto;overflow:hidden}
main[data-view="agent"] #agent-panel{display:flex}
main[data-view="agent"] #feed,main[data-view="agent"] #composer{display:none}
.agent-bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.agent-bar .pill{font-size:11px}
.agent-log{flex:1 1 auto;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:10px;
  padding:4px 2px 8px}
.agent-bubble{max-width:min(760px,92%);padding:10px 13px;border-radius:var(--radius);
  border:1px solid var(--line);background:var(--bg-card);color:var(--text);font-size:14px;line-height:1.6;
  white-space:pre-wrap;word-break:break-word}
.agent-bubble.user{align-self:flex-end;background:var(--bg-hover);border-color:var(--line)}
.agent-bubble.agent p{margin:0}
.agent-bubble.err{border-color:color-mix(in srgb,var(--red) 35%,var(--line))}
.agent-bubble.dim{color:var(--text-mid)}
.agent-notes{margin-top:8px;display:flex;flex-direction:column;gap:3px}
.agent-notes span{color:var(--text-dim);font-size:12px}
.agent-tasks{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}
.agent-docs{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.agent-doc{display:flex;align-items:center;gap:8px;padding:7px 10px;border:1px solid var(--line);
  border-radius:var(--radius-sm);background:var(--bg-hover);text-decoration:none;color:var(--text);
  font-size:13px;line-height:1.4}
.agent-doc:hover{border-color:var(--blue)}
.agent-doc b{font-weight:600}
.agent-doc span{color:var(--text-dim);font-size:12px}
.agent-docs em{color:var(--text-dim);font-size:12px;font-style:normal}
.agent-actions{margin-top:8px;display:flex;gap:6px}
.agent-io{display:flex;gap:8px;align-items:flex-end}
.agent-io textarea{flex:1 1 auto;resize:vertical;min-height:64px;max-height:220px;
  background:var(--bg-card);border:1px solid var(--line);border-radius:var(--radius-sm);
  color:var(--text);font:inherit;font-size:14px;padding:10px 12px;line-height:1.55}
.agent-io textarea:focus{outline:none;border-color:var(--blue)}
.agent-send{background:var(--accent);color:var(--accent-ink);border:1px solid var(--accent);
  border-radius:var(--radius-sm);padding:10px 16px;font:inherit;font-size:14px;font-weight:600;cursor:pointer}
.agent-send:disabled{opacity:.5;cursor:default}
.agent-ref{display:none;align-items:center;gap:8px;color:var(--text-dim);font-size:12px}
.agent-ref.on{display:flex}
.agent-ref img{width:44px;height:44px;object-fit:cover;border-radius:6px;border:1px solid var(--line)}
.agent-chips{display:flex;flex-wrap:wrap;gap:6px}
.agent-hint{color:var(--text-dim);font-size:12px;margin:0}
/* 默认收起的两块：帮助长文与调试读数。点开才占地方，首屏保持干净。 */
.agent-help{display:none;max-height:42vh;overflow:auto;padding:10px 13px;border:1px solid var(--line);
  border-radius:var(--radius);background:var(--bg-hover);color:var(--text-mid);font-size:13px;
  line-height:1.65;white-space:pre-wrap;word-break:break-word}
.agent-help.on{display:block}
.agent-toggle[aria-pressed="true"]{border-color:var(--blue);color:var(--blue)}
.agent-reprobe{background:transparent;border:1px dashed var(--line);border-radius:999px;
  color:var(--text-mid);font:inherit;font-size:11px;padding:2px 10px;cursor:pointer}
.agent-reprobe:hover{border-color:var(--blue);color:var(--blue)}
.agent-reprobe[disabled]{opacity:.55;cursor:default}
`;

  function ensureStyle() {
    if (document.getElementById("chenguai-agent-style")) return;
    const style = document.createElement("style");
    style.id = "chenguai-agent-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  // ------------------------------------------------------------------ 构建

  function build() {
    ensureStyle();
    if (panel) return;
    const tabs = document.getElementById("mode-tabs");
    if (!tabs) return;
    panel = node(
      '<div id="agent-panel">' +
        '<div class="agent-bar" id="agent-bar"></div>' +
        '<div class="agent-log" id="agent-log"></div>' +
        '<div class="agent-help" id="agent-help"></div>' +
        '<div class="agent-ref" id="agent-ref"></div>' +
        '<div class="agent-chips" id="agent-chips"></div>' +
        '<div class="agent-io">' +
        '<textarea id="agent-text" rows="2" placeholder="描述你想要的画面、视频或音乐，也可以让我写文案、做 Word/Excel/PPT 文件、聊天或朗读…"></textarea>' +
        '<button class="agent-send" id="agent-send" type="button">发送</button>' +
        "</div>" +
        '<p class="agent-hint" id="agent-hint"></p>' +
        "</div>"
    );
    tabs.insertAdjacentHTML("afterend", panel.outerHTML);
    panel = document.getElementById("agent-panel");
    logEl = document.getElementById("agent-log");
    textEl = document.getElementById("agent-text");
    sendBtn = document.getElementById("agent-send");
    statusEl = document.getElementById("agent-bar");
    refEl = document.getElementById("agent-ref");

    sendBtn.addEventListener("click", () => send());
    textEl.addEventListener("keydown", (event) => {
      // 中文输入法组字期间的 Enter 只做确认候选，不能提交。
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        send();
      }
    });
    const chips = document.getElementById("agent-chips");
    for (const example of EXAMPLES) {
      const chip = node('<button class="chip" type="button"></button>');
      chip.textContent = example;
      chip.addEventListener("click", () => {
        textEl.value = example;
        textEl.focus();
      });
      chips.appendChild(chip);
    }
    const clear = node('<button class="btn-mini" id="agent-clear" type="button">清空对话</button>');
    clear.addEventListener("click", () => {
      history = [];
      logEl.replaceChildren();
    });
    chips.appendChild(clear);
    // 帮助与调试信息：都是「默认收起、点了才展开」的开关，界面文字本身不随状态变化，
    // 状态靠 aria-pressed + 描边颜色表达，切语言时也不会掉回中文。
    const helpToggle = node('<button class="btn-mini agent-toggle" id="agent-help-toggle" type="button" aria-pressed="false">帮助</button>');
    helpToggle.addEventListener("click", () => toggleHelp());
    chips.appendChild(helpToggle);
    const debugToggle = node('<button class="btn-mini agent-toggle" id="agent-debug-toggle" type="button" aria-pressed="false">调试信息</button>');
    debugToggle.addEventListener("click", () => {
      debugOn = !debugOn;
      renderToggles();
      renderHint();
    });
    chips.appendChild(debugToggle);
    const pick = node('<button class="btn-file" id="agent-pick" type="button">🖼 选择垫图</button>');
    const file = node('<input type="file" id="agent-file" accept="image/*" class="hidden">');
    pick.addEventListener("click", () => file.click());
    file.addEventListener("change", () => {
      const chosen = file.files && file.files[0];
      if (chosen) uploadRef(chosen);
      file.value = "";
    });
    chips.appendChild(pick);
    chips.appendChild(file);
  }

  // -------------------------------------------------- 帮助 / 调试信息（默认收起）

  function renderToggles() {
    const helpToggle = document.getElementById("agent-help-toggle");
    const debugToggle = document.getElementById("agent-debug-toggle");
    if (helpToggle) helpToggle.setAttribute("aria-pressed", helpOpen ? "true" : "false");
    if (debugToggle) debugToggle.setAttribute("aria-pressed", debugOn ? "true" : "false");
  }

  function renderHelp() {
    const box = document.getElementById("agent-help");
    if (!box) return;
    const text = (caps && caps.help) || "";
    box.textContent = text;
    box.classList.toggle("on", Boolean(helpOpen && text));
  }

  function toggleHelp() {
    helpOpen = !helpOpen;
    renderToggles();
    if (helpOpen && !caps) {
      refreshCapabilities(true).then(() => renderHelp());
      return;
    }
    renderHelp();
  }

  // 底部那行：默认只有人话提示（notes）；打开「调试信息」才把显存读数（diagnostics）拼上。
  function renderHint() {
    const hint = document.getElementById("agent-hint");
    if (!hint) return;
    const notes = (caps && caps.notes) || [];
    const diagnostics = (caps && caps.diagnostics) || [];
    const parts = debugOn ? notes.concat(diagnostics) : notes.slice();
    hint.textContent = parts.join(" · ");
  }

  // ------------------------------------------------------------- 能力条

  async function refreshCapabilities(force) {
    if (caps && !force) {
      renderCapabilities();
      renderHelp();
      renderHint();
      return caps;
    }
    if (probing) return caps;
    probing = true;
    try {
      caps = await getJson(ENDPOINTS.capabilities);
    } catch (error) {
      caps = { engines: {}, llm: { available: false, note: `能力查询失败：${error.message}` }, tts: { available: false, voices: [] }, notes: [], help: "" };
    }
    probing = false;
    renderCapabilities();
    renderHelp();
    renderHint();
    // 降级不是终局：控制台刚启动时引擎还在注册、专用大模型实例也可能晚几秒才起来。
    if (capabilitiesDegraded(caps)) {
      scheduleProbeRetry();
    } else {
      probeRetries = 0;
    }
    return caps;
  }

  function capabilitiesDegraded(data) {
    const engines = (data && data.engines) || {};
    const llm = (data && data.llm) || {};
    const voices = ((data && data.tts) || {}).voices || [];
    // 与桌面端同一组板块槽：少一个就算「还没就位」，会自动再探测。
    const missing = ["image", "i2i", "t2v", "i2v", "music"].filter((key) => !engines[key]);
    return missing.length > 0 || !llm.available || !voices.length;
  }

  function scheduleProbeRetry() {
    if (probeRetries >= PROBE_RETRIES) return;
    probeRetries += 1;
    setTimeout(() => { refreshCapabilities(true); }, PROBE_RETRY_MS);
  }

  function reprobeButton() {
    const button = node('<button class="agent-reprobe" type="button">重新探测</button>');
    button.addEventListener("click", () => {
      probeRetries = 0;
      button.textContent = "正在重新探测…";
      button.disabled = true;
      refreshCapabilities(true).then(() => {
        button.textContent = "重新探测";
        button.disabled = false;
      });
    });
    return button;
  }

  function pill(text, kind) {
    const span = node(`<span class="pill ${kind || ""}"></span>`);
    span.textContent = text;
    return span;
  }

  function renderCapabilities() {
    if (!statusEl || !caps) return;
    statusEl.replaceChildren();
    const engines = caps.engines || {};
    const states = caps.engine_states || {};
    // 槽位、标签与顺序必须与桌面端一字不差（app/ui/pages/agent_page.py）：
    // 文生图与图生图是两个独立引擎，各占一个槽。
    const names = { image: "文生图", i2i: "图生图", t2v: "文生视频", i2v: "图生视频", music: "音乐" };
    for (const [key, label] of Object.entries(names)) {
      const engine = engines[key];
      if (!engine) {
        statusEl.appendChild(pill(`${label}：无引擎`, "bad"));
      } else if (["ready", "busy", "starting"].includes(states[engine] || "")) {
        statusEl.appendChild(pill(`${label}：${engine}`, "ok"));
      } else {
        // 已注册、只是当前没把模型搬进显存：正常待命，不等于「没有引擎」。
        statusEl.appendChild(pill(`${label}：${engine} · 未启动`, ""));
      }
    }
    const llm = caps.llm || {};
    if (llm.available) {
      statusEl.appendChild(pill(`本机大模型：${llm.model || ""}`, "ok"));
    } else {
      statusEl.appendChild(pill("本机大模型：不可用", "bad"));
    }
    const tts = caps.tts || {};
    const voices = tts.voices || [];
    statusEl.appendChild(pill(`系统语音：${voices.length ? `${voices.length} 个声音` : "不可用"}`, voices.length ? "ok" : "bad"));
    statusEl.appendChild(reprobeButton());
  }

  // -------------------------------------------------------------- 气泡渲染

  function addBubble(role, text, meta) {
    if (!logEl) return null;
    const bubble = node(`<div class="agent-bubble ${role}"></div>`);
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    bubble.appendChild(paragraph);
    const notes = (meta && meta.notes) || [];
    if (notes.length) {
      const wrap = node('<div class="agent-notes"></div>');
      for (const note of notes) {
        const item = document.createElement("span");
        item.textContent = `· ${note}`;
        wrap.appendChild(item);
      }
      bubble.appendChild(wrap);
    }
    const tasks = (meta && meta.tasks) || [];
    if (tasks.length) {
      const row = node('<div class="agent-tasks"></div>');
      for (const task of tasks) {
        const tab = MODE_TO_TAB[task.task_type] || MODE_TO_TAB[meta.mode] || "t2i";
        const button = node('<button class="btn-mini" type="button"></button>');
        button.textContent = `→ ${String(task.task_id).slice(0, 8)} · ${task.engine_id}`;
        button.title = task.prompt;
        button.addEventListener("click", () => {
          close();
          const tabButton = document.querySelector(`.tab[data-mode="${tab}"]`);
          if (tabButton) tabButton.click();
        });
        row.appendChild(button);
      }
      bubble.appendChild(row);
    }
    const documents = (meta && meta.documents) || [];
    if (documents.length) {
      // 生成的 .docx/.xlsx/.pptx：点一下下载（后端按 /outputs/ 只读路由发文件）。
      const row = node('<div class="agent-docs"></div>');
      for (const doc of documents) {
        const entry = node(doc.url ? '<a class="agent-doc"></a>' : '<div class="agent-doc"></div>');
        if (doc.url) {
          entry.href = `${doc.url}?download=1`;
          entry.setAttribute("target", "_blank");
          entry.setAttribute("rel", "noopener");
          entry.download = doc.name || "";
        }
        const title = document.createElement("b");
        title.textContent = `⬇ ${doc.name || "文件"}`;
        const detail = document.createElement("span");
        const size = doc.bytes ? `${Math.max(1, Math.round(doc.bytes / 1024))} KB` : "";
        detail.textContent = [doc.label || "", doc.stats || "", size].filter(Boolean).join(" · ");
        entry.appendChild(title);
        entry.appendChild(detail);
        entry.title = doc.path || "";
        row.appendChild(entry);
      }
      const note = document.createElement("em");
      note.textContent = "文件写在输出目录里，项目内 output\\ 与「自动另存」目录也各放一份；重名会加 -1，不覆盖旧文件";
      row.appendChild(note);
      bubble.appendChild(row);
    }
    if (role.indexOf("user") === -1 && text && text.length > 1) {
      const actions = node('<div class="agent-actions"></div>');
      const speak = node('<button class="btn-mini" type="button">朗读</button>');
      speak.addEventListener("click", () => speakText(text));
      actions.appendChild(speak);
      bubble.appendChild(actions);
    }
    logEl.appendChild(bubble);
    logEl.scrollTop = logEl.scrollHeight;
    return bubble;
  }

  async function speakText(text) {
    try {
      const data = await postJson(ENDPOINTS.command, { message: text, action_hint: "speak" });
      notify(data.reply || "…", data.error ? "error" : "success");
    } catch (error) {
      notify(`朗读失败：${error.message}`, "error");
    }
  }

  // -------------------------------------------------------------- 垫图上传

  async function uploadRef(file) {
    if (!file.type || !file.type.startsWith("image/")) {
      notify("只能上传图片文件", "error");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      notify("图片超过 8MB，请先缩小", "error");
      return;
    }
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("读取图片失败"));
        reader.readAsDataURL(file);
      });
      notify("正在上传垫图…", "");
      const saved = await postJson(ENDPOINTS.uploads, {
        name: file.name,
        data: dataUrl.split(",")[1] || "",
      });
      refImage = saved;
      renderRef();
      notify(`垫图已就绪：${saved.name}`, "success");
    } catch (error) {
      refImage = null;
      renderRef();
      notify(`上传失败：${error.message}`, "error");
    }
  }

  function renderRef() {
    if (!refEl) return;
    if (!refImage) {
      refEl.classList.remove("on");
      refEl.replaceChildren();
      return;
    }
    refEl.classList.add("on");
    const image = document.createElement("img");
    image.src = refImage.url;
    image.alt = refImage.name;
    const label = document.createElement("span");
    label.textContent = refImage.name;
    const remove = node('<button class="btn-mini" type="button">✕</button>');
    remove.addEventListener("click", () => {
      refImage = null;
      renderRef();
    });
    refEl.replaceChildren(image, label, remove);
  }

  // ---------------------------------------------------------------- 发送

  async function send() {
    const message = (textEl.value || "").trim();
    if (!message || busy) return;
    busy = true;
    sendBtn.disabled = true;
    addBubble("user", message, {});
    textEl.value = "";
    const thinking = addBubble("agent dim", "…", {});
    try {
      const data = await postJson(ENDPOINTS.command, {
        message,
        history,
        ref_image: refImage ? refImage.path : "",
        action_hint: "",
      });
      if (thinking && thinking.parentNode) thinking.remove();
      const meta = {
        notes: data.notes || [],
        tasks: data.tasks || [],
        documents: data.documents || [],
        mode: data.mode,
      };
      const role = data.error ? "agent err" : "agent";
      addBubble(role, data.reply || (data.error ? data.error : "…"), meta);
      history.push({ role: "user", content: message });
      history.push({ role: "assistant", content: data.reply || "" });
      while (history.length > HISTORY_LIMIT) history.shift();
      if (data.tasks && data.tasks.length) {
        // 任务进的是与手动提交同一条 FIFO 队列，但走的是 /api/agent/command，
        // 不经过 app.js 的提交流程：不补这一步，智能体排队的产物就不会出现在会话流里。
        const kind = data.modality === "music" ? "music" : data.modality === "video" ? "video" : "imagine";
        for (const task of data.tasks) {
          if (typeof window.ChenGuAttachTask === "function") {
            window.ChenGuAttachTask(task, {
              kind,
              mode: data.mode || (kind === "music" ? "music" : kind === "video" ? "t2v" : "t2i"),
              prompt: task.prompt || message,
              params: task.parameters || {},
              refPath: refImage ? refImage.path : "",
            });
          }
        }
        // 任务进了同一条队列，切到对应板块看进度最直观。
        notify(data.reply, "success");
      }
    } catch (error) {
      if (thinking && thinking.parentNode) thinking.remove();
      addBubble("agent err", `请求失败：${error.message}`, {});
    } finally {
      busy = false;
      sendBtn.disabled = false;
      textEl.focus();
    }
  }

  // ------------------------------------------------------------- 开合控制

  function isOpen() {
    return open;
  }

  function openPanel() {
    build();
    if (open) return;
    open = true;
    const main = document.getElementById("main");
    if (typeof state !== "undefined" && state) {
      previousMode = state.mode === "agent" ? previousMode : state.mode;
      state.mode = "agent";
      if (typeof renderTabs === "function") renderTabs();
    }
    if (main) main.dataset.view = "agent";
    refreshCapabilities();
    setTimeout(() => textEl && textEl.focus(), 30);
  }

  function closePanel() {
    if (!open) return;
    open = false;
    const main = document.getElementById("main");
    if (main) main.dataset.view = "";
    if (typeof state !== "undefined" && state && state.mode === "agent") {
      if (typeof setMode === "function") setMode(previousMode || "t2i");
      else state.mode = previousMode || "t2i";
      if (typeof renderTabs === "function") renderTabs();
    }
  }

  window.ChenGuAgent = {
    open: openPanel,
    close: closePanel,
    isOpen: isOpen,
    refresh: () => refreshCapabilities(true),
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
