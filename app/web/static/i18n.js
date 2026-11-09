/* 语言菜单（🌏）与界面翻译。
 *
 * 设计：界面源代码一律写中文（源语言 = zh），词典文件放 app/web/static/i18n/<code>.json，
 * key 就是界面上出现的那句中文，value 是目标语言。这样不用把 app.js（1400 行）改造成
 * key-based 模板，也能让动态渲染出来的节点（任务卡片、toast、状态 pill）跟着换语言。
 *
 * 覆盖范围：界面 chrome（Tab、按钮、面板标题、输入框提示、状态文案、toast）。
 * 引擎回报的错误信息（显存不足、引擎未注册等）保持中文原样，不做机翻式伪装。
 * 桌面端读同一份词典（app/core/i18n.py），两端语言各自持久化：网页 localStorage，
 * 桌面写 config.json 的 ui_language。
 */
(function () {
  "use strict";

  const STORE_KEY = "chenguai.lang";
  const I18N_DIR = "/static/i18n";
  // 动态消息的拼接片段（例如「排队中 · 第 3 位」），只允许这些做片段替换，
  // 避免把长句里的普通词硬凑替换。
  const FRAGMENT_KEYS = ["排队中 · 第", "生成中", "位"];
  const ATTR_KEYS = ["title", "placeholder", "alt", "aria-label"];
  const CJK = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

  const I18N = {
    lang: "zh",
    languages: [],
    dicts: new Map(),
    dir: "ltr",
    ready: false,
    t: translate,
    setLanguage: setLanguage,
    languagesList() {
      return I18N.languages;
    },
  };
  window.ChenGuI18n = I18N;

  // ------------------------------------------------------------------ 词典

  function readStored() {
    try {
      return localStorage.getItem(STORE_KEY) || "zh";
    } catch (error) {
      return "zh";
    }
  }

  function writeStored(code) {
    try {
      localStorage.setItem(STORE_KEY, code);
    } catch (error) {
      /* 隐私模式下 localStorage 不可写：本次会话内仍然生效即可 */
    }
  }

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function loadLanguages() {
    if (I18N.languages.length) return I18N.languages;
    try {
      const list = await getJson(`${I18N_DIR}/index.json`);
      if (Array.isArray(list)) I18N.languages = list;
    } catch (error) {
      I18N.languages = [{ code: "zh", native: "简体中文", english: "Chinese", dir: "ltr" }];
    }
    return I18N.languages;
  }

  async function loadDict(code) {
    if (I18N.dicts.has(code)) return I18N.dicts.get(code);
    const map = new Map();
    if (code !== "zh") {
      try {
        const raw = await getJson(`${I18N_DIR}/${code}.json`);
        for (const [key, value] of Object.entries(raw || {})) {
          // 空串是合法翻译（例如「位」这类后缀片段在法语/德语里不需要），
          // 只有非字符串才丢掉；exact 匹配会跳过空串，片段替换会用它删掉中文残留。
          if (typeof value === "string") map.set(key, value);
        }
      } catch (error) {
        // 词典缺失或损坏：老实退回中文，不假装已翻译。
        console.warn(`[i18n] 缺少 ${code} 词典，界面回退中文：${error.message}`);
      }
    }
    I18N.dicts.set(code, map);
    return map;
  }

  let table = null;
  let fragments = [];

  function translate(text) {
    if (!text) return text;
    if (!table || table.size === 0) return text;
    const exact = table.get(text);
    if (exact) return exact;
    if (!CJK.test(text)) return text;
    let out = text;
    for (const key of fragments) {
      if (out.includes(key) && table.has(key)) out = out.split(key).join(table.get(key));
    }
    return out;
  }

  // -------------------------------------------------------------- DOM 换语言

  let processed = new WeakMap();
  let queue = [];
  let timer = 0;

  function flushQueue() {
    timer = 0;
    const records = queue;
    queue = [];
    for (const record of records) {
      if (record.type === "characterData") {
        translateNode(record.target);
        continue;
      }
      for (const node of record.addedNodes || []) {
        if (node.nodeType === Node.ELEMENT_NODE) scanElement(node);
        else if (node.nodeType === Node.TEXT_NODE) translateNode(node);
      }
      if (record.type === "attributes") scanElement(record.target);
    }
  }

  function schedule(records) {
    queue.push(...records);
    if (!timer) timer = setTimeout(flushQueue, 40);
  }

  function translateNode(node) {
    const current = node.nodeValue || "";
    if (processed.get(node) === current) return;
    processed.set(node, current);
    const next = translate(current);
    if (next !== current) {
      processed.set(node, next);
      node.nodeValue = next;
    }
  }

  function scanElement(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return;
    const tag = element.tagName;
    if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA" || tag === "CODE") return;
    for (const name of ATTR_KEYS) {
      const value = element.getAttribute(name);
      if (!value) continue;
      const next = translate(value);
      if (next !== value) element.setAttribute(name, next);
    }
    for (const child of element.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) translateNode(child);
      else if (child.nodeType === Node.ELEMENT_NODE) scanElement(child);
    }
  }

  function scanAll() {
    scanElement(document.documentElement);
    document.title = translate(document.title) || document.title;
  }

  async function setLanguage(code, options = {}) {
    const list = await loadLanguages();
    const entry = list.find((item) => item.code === code) || list[0] || { code: "zh", dir: "ltr" };
    I18N.lang = entry.code;
    I18N.dir = entry.dir || "ltr";
    table = await loadDict(I18N.lang);
    fragments = FRAGMENT_KEYS.filter((key) => table.has(key)).sort(
      (a, b) => b.length - a.length
    );
    processed = new WeakMap();
    document.documentElement.lang = I18N.lang;
    document.documentElement.setAttribute("data-lang", I18N.lang);
    document.body.classList.toggle("rtl", I18N.dir === "rtl");
    scanAll();
    if (options.persist !== false) writeStored(I18N.lang);
    renderMenuState();
    if (typeof window.showToast === "function" && options.notify !== false && I18N.lang !== "zh") {
      const entry2 = list.find((item) => item.code === I18N.lang);
      window.showToast(`界面语言：${entry2 ? entry2.native : I18N.lang}`, "success");
    }
    return I18N.lang;
  }

  // ------------------------------------------------------------------ 菜单

  const STYLE = `
.lang-menu{position:relative;display:inline-flex}
.lang-list{position:absolute;right:0;top:calc(100% + 8px);background:var(--bg-card);
  border:1px solid var(--line);border-radius:var(--radius-sm);padding:6px;min-width:210px;
  max-height:62vh;overflow:auto;z-index:70;box-shadow:0 14px 34px rgba(0,0,0,.5)}
.lang-list .lang-head{padding:4px 8px 6px;color:var(--text-dim);font-size:11px;letter-spacing:.06em}
.lang-list button{display:flex;justify-content:space-between;align-items:center;gap:12px;width:100%;
  padding:7px 9px;background:none;border:0;border-radius:6px;color:var(--text-mid);font:inherit;
  font-size:13px;text-align:left;cursor:pointer}
.lang-list button:hover{background:var(--bg-hover);color:var(--text)}
.lang-list button.on{background:var(--bg-hover);color:var(--text)}
.lang-list button .en{color:var(--text-dim);font-size:11px}
body.rtl{text-align:right}
body.rtl .pill,body.rtl .tab,body.rtl .chip{text-align:right}
`;

  function ensureStyle() {
    if (document.getElementById("chenguai-i18n-style")) return;
    const style = document.createElement("style");
    style.id = "chenguai-i18n-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  let listEl = null;

  function renderMenuState() {
    if (!listEl) return;
    for (const button of listEl.querySelectorAll("button[data-lang-code]")) {
      button.classList.toggle("on", button.dataset.langCode === I18N.lang);
      button.setAttribute("aria-selected", button.dataset.langCode === I18N.lang ? "true" : "false");
    }
    const codeEl = document.getElementById("lang-tag");
    if (codeEl) {
      const entry = I18N.languages.find((item) => item.code === I18N.lang);
      codeEl.textContent = entry ? entry.native : I18N.lang;
    }
  }

  function buildMenu() {
    ensureStyle();
    const anchor = document.getElementById("toggle-settings");
    if (!anchor || document.getElementById("lang-btn")) return;
    const wrap = document.createElement("div");
    wrap.className = "lang-menu";
    wrap.innerHTML =
      '<button class="icon-btn" id="lang-btn" title="语言" aria-haspopup="listbox">🌏 <span id="lang-tag">中文</span></button>' +
      '<div class="lang-list" id="lang-list" role="listbox" hidden></div>';
    anchor.before(wrap);
    listEl = wrap.querySelector("#lang-list");
    const head = document.createElement("div");
    head.className = "lang-head";
    head.textContent = "当前语言";
    listEl.appendChild(head);
    for (const entry of I18N.languages) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.langCode = entry.code;
      button.setAttribute("role", "option");
      button.innerHTML =
        `<span>${entry.native || entry.code}</span><span class="en">${entry.english || ""}</span>`;
      button.addEventListener("click", () => {
        listEl.hidden = true;
        setLanguage(entry.code).catch((error) => {
          if (typeof window.showToast === "function") {
            window.showToast(`切换语言失败：${error.message}`, "error");
          }
        });
      });
      listEl.appendChild(button);
    }
    const trigger = wrap.querySelector("#lang-btn");
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      listEl.hidden = !listEl.hidden;
      renderMenuState();
    });
    document.addEventListener("click", (event) => {
      if (!listEl.hidden && !wrap.contains(event.target)) listEl.hidden = true;
    });
    renderMenuState();
  }

  // ------------------------------------------------------------------ 启动

  async function boot() {
    await loadLanguages();
    buildMenu();
    if (!window.ChenGuI18n._observer) {
      window.ChenGuI18n._observer = new MutationObserver((records) => schedule(records));
      window.ChenGuI18n._observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: ATTR_KEYS,
      });
    }
    const stored = readStored();
    if (stored && stored !== "zh") {
      await setLanguage(stored, { persist: false, notify: false });
    } else {
      I18N.lang = "zh";
      table = new Map();
      renderMenuState();
    }
    I18N.ready = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      boot().catch((error) => console.warn(`[i18n] 初始化失败：${error.message}`));
    });
  } else {
    boot().catch((error) => console.warn(`[i18n] 初始化失败：${error.message}`));
  }
})();
