# 辰鹄 AI 创作中心（Chengu AI Studio）

> 本地化 AI 图片/视频/音频生成平台

## 📊 项目状态概览

| 阶段 | 状态 | 描述 |
|------|------|------|
| **Phase 0** | ✅ 已完成 | 引擎调研、许可证审计、硬件兼容性分析、架构提案、开发计划 |
| **Phase 1** | ✅ 已完成 | 桌面骨架与本地基础（PySide6 UI、SQLite、配置服务） |
| **Phase 2** | ✅ 已完成 | 任务系统核心（FIFO 队列、状态管理、引擎生命周期隔离） |
| **Phase 3** | 🚧 收尾中（2026-11-10 换模型完成；六能力真机验收 **3/6**：图生图、文生视频、AI 智能体已通过，**文生图换型为 Sana 后需重新实测峰值**，图转视频与 AI 音乐在等「显存＋空闲物理内存」双窗口，实测本机空闲显存 1.3GB / 空闲内存 2.1GB） | 图像引擎现职为 **Sana-1.6B 多语言版**：文生图 `sana-multiling`（Apache-2.0，文本编码器 Gemma-2-2B-IT 按 Gemma Terms of Use，权重 9.06GB 在 `models\sana-multiling`，独立 worker 子进程跑在 `.venv-image`），**文生图一次 1 张**，1024 级直出、超单遍像素预算（1.10MP）走「底图 + Lanczos 放大」到 1080p（1920×1088，元数据 `upscaled=true`、`tiles=0`）；**显存峰值 ≤6GB 目前是按 fp16 权重体积推算的预期值，真机未实测**（验收 `scripts/acceptance_image.bat`）。图生图 **`wan-i2i`**（Wan2.1-VACE-1.3B 参考重绘单帧，与视频共用 `.venv-video` 与 `models/wan2.1`，零新增下载）；**真 FLUX.1-schnell 与 SD1.5/RealVis 均已退出**（FLUX 2026-11-08 整套删除、腾出 31.41GB；SD1.5 三件套 4.41GB 留盘作后备但未注册、界面不可见，退场原因是选型不是许可）；**视频引擎 Wan2.1 1.3B**（`wan-t2v` 文生视频 / `wan-i2v` 图转视频·VACE 首帧条件，Apache-2.0，权重 44.18GB 在 `models/wan2.1`）已改「逐层卸载 + token 预算」把峰值压进 6GB 档（实测 2021MB）；旧「文生图 `wan-t2i`」2026-11-10 下架（受 480p 视频档像素预算限制），历史任务记录不删；**AI 音乐 ACE-Step 1.5**（MIT，9.40GB，`models/ace-step-1.5`）；**AI 智能体**用项目内 `tools/llama_cpp` + `models/llm/gguf/Qwen3-8B-Q4_K_M.gguf`（Apache-2.0，**不用 Ollama**）；Web 控制台（三栏 + 五模式 Tab）与桌面端十页同时可用 |

**重要提示**（2026-11-10 同步）：图片引擎现在是 **Sana-1.6B 多语言版**（`sana-multiling`，Apache-2.0；文本编码器 Gemma-2-2B-IT 按 Gemma Terms of Use、DC-AE VAE 上游许可待复核；权重 9.06GB 在 `models\sana-multiling`，只取 fp16 变体，下载脚本 `scripts/download_sana_weights.py` 走 hf-mirror、断点续传）。**中英文提示词原生支持**，不需要任何桥接——多语言编码器直接吃中文，模型卡原文「supports Emoji, Chinese and English and all mixed prompts」，产物元数据里的 `prompt_translated` 恒为 false（历史上 SD1.5 那套离线中英桥接表 `app/engine/prompt_bilingual.py` 已不在现役链路，只被未注册的 `sd15.py` 与它的测试引用）。文生图**一次 1 张**，滑杆 320-2048、步长 32（Sana 的 VAE 是 32× 下采样，倍数不对会静默漂移，所以强制 32），单边不超过 2048；默认 1024×1024、steps 20（允许 10-40）、CFG 固定 4.5（多语言版官方推荐值，界面不给调，避免误用导致画面崩坏）。超单遍像素预算 1.10MP 时按预算出底图再 Lanczos 放大到目标尺寸（元数据 `upscaled=true`、`tiles=0`，不谎称原生 1080p）。**铁律：所有大模型可商用、中英文 prompt、峰值显存不超 6GB**——Sana 靠 fp16 逐模块换入（任一时刻显存里只有当前一件，最大单件 4.99GB）达成，这一条**尚未真机实测**。视频引擎提交前自动做显存预检，并按 token 预算（上限 20000，见 `app/engine/video/budget.py`）判定档位：**档位钉死，不自动降档**——你选 832×480 就出 832×480；超预算的组合在提交前就被拦下，并给出「保分辨率减帧」和「保帧数降尺寸」两条出路，由你自己选一项。引擎不会偷偷改尺寸、不会偷改帧数，也不会只往日志里写一行就悄悄把画幅缩小；引擎未注册时拒绝提交，程序不伪造任何占位图或模拟输出。**桌面端与网页端能力条、引擎 id、参数白名单、显存门槛逐字一致**（共用同一 `TaskService`）。真机端到端验收完成前勿视为正式产出。桌面界面跟随 Windows 深/浅色主题自动切换。

**已知运行约束**（2026-11-10 口径）：Sana 的显存闸门写在引擎里——空闲显存低于约 **5120MB** 时自动退到「逐层换入」（慢约两三倍但能跑），低于约 **1500MB** 直接拒绝并点名是谁占着显存；加载权重另有一道物理内存闸门，要求空闲内存 **≥16GB**（历史上 Wan 在内存不足时被系统静默杀掉过，这道闸门就是那次之后加的）。视频与音乐仍需先释放显存：双击 `scripts/release_local_engine.bat` 只结束**本项目自己**的引擎进程（本地大模型空闲 10 分钟也会自动卸载，控制台也有「释放空闲引擎」按钮）；**绝不会去杀第三方程序**。文生图与视频、音乐共用同一条 FIFO 串行队列，一次只跑一个任务，界面上的排队与「前面还有 N 项」照实显示。视频引擎为 **Wan2.1 1.3B**（Apache-2.0；原 Anima 引用及其无效下载入口已于 2026-11-05 移除，真实上游 circlestone-labs/Anima 非商用，不下载、不入包）。

---

## 🎯 核心目标

- **单一中文桌面入口**：通过 PySide6 构建 Windows 本地化应用
- **引擎独立与隔离**：每个生成模态（图片/视频/音频）使用独立 venv 和子进程，崩溃不影响 UI
- **离线优先推理**：不依赖网络服务，所有生成在本机完成
- **严格许可证合规**：代码与模型权重分别审查、许可隔离管理
- **真实能力验证**：只有经过本机测试并通过的引擎才能在 UI 中启用

---

## 📁 项目结构

```text
Third Project/
├── app/                          # 核心应用代码（桌面程序）
│   ├── __init__.py              # 应用版本信息
│   ├── bootstrap.py             # 启动入口、服务注册、生命周期管理
│   ├── engine_registration.py   # 真实引擎注册（Sana 文生图 / Wan2.1 文生视频·图生视频·图生图 / ACE-Step 音乐 / 智能体 worker；五个引擎 id + 4200s 超时）
│   ├── engine/prompt_utils.py   # 提示词类型规整（任意来源输入 → 干净字符串，零重依赖）
│   ├── engine/prompt_bilingual.py # （历史遗留）中文→英文关键词桥接（SD1.5 的 CLIP 没有中日韩词表才需要；纯标准库、离线、零显存。现役 Sana 用不上，只剩未注册的 sd15.py 与它的测试引用它）
│   ├── core/                    # 核心服务层
│   │   ├── config.py           # JSON 配置持久化与验证
│   │   ├── hardware.py         # GPU/磁盘观察与运行前检查
│   │   ├── logging_setup.py    # 日志轮转与本地存储
│   │   └── paths.py            # 路径规范化与应用目录结构
│   ├── database/                # SQLite 数据库与迁移管理
│   │   ├── __init__.py
│   │   └── database.py         # Schema 版本化迁移（v1→v2）
│   ├── engine/                  # 引擎抽象接口
│   │   ├── __init__.py
│   │   ├── abstract.py         # EngineAdapter Protocol
│   │   ├── image/              # 图片引擎 adapter（现役：Sana-1.6B 多语言版；SD1.5 那套代码保留但不注册）
│   │       ├── __init__.py     # PEP 562 惰性导出（没装 torch 的 .venv 也能 import app.engine.image）
│   │       ├── plan.py         # 出图预算的唯一真相源，纯标准库（SANA_PROFILE：32 倍数、单边 2048、单遍 1.10MP、目标 4.20MP；SD15_PROFILE 保留）
│   │       ├── sana.py         # 现役文生图引擎：fp16 逐模块换入、显存两档闸门（5120/1500MB）+ 内存闸门（≥16GB）、steps 10-40、CFG 4.5、超预算走底图+Lanczos 放大
│   │       ├── hires.py        # 分块网格、羽化融合 TileBlender、Lanczos 放大（Sana 只用其中的放大，不用分块重绘）
│   │       └── sd15.py         # （未注册后备）SD1.5 引擎：文生图 / 图生图 / 分块真 1080p / 显存分档卸载
│   │   └── video/              # 视频引擎 adapter（wan.py：Wan2.1 T2V / VACE）
│   ├── models/                  # 模型管理（待 Phase 5 实现）
│   │   ├── __init__.py
│   │   ├── manager.py          # 模型清单、校验与下载确认
│   │   └── registry.py         # 模型注册表
│   ├── tasks/                   # 任务系统核心
│   │   ├── __init__.py
│   │   ├── engine_manager.py   # 引擎生命周期管理（懒启动、健康检查）
│   │   ├── sana_worker.py      # 现役文生图 worker（生产适配器，运行于 .venv-image 子进程；参数白名单 width/height/steps/seed/negative_prompt，hires/init_image_path 明确拒绝）
│   │   ├── sd_worker.py        # （未注册后备）SD1.5 图像 worker，留盘不接线
│   │   ├── music_worker.py     # ACE-Step 音乐 worker（运行于 .venv-music 子进程）
│   │   ├── video_worker.py     # Wan2.1 视频 worker（运行于 .venv-video 子进程）
│   │   ├── models.py           # TaskRequest/TaskResult/TaskRecord/TaskStatus
│   │   ├── process_adapter.py  # ProcessEngineAdapter 子类定义
│   │   ├── process_worker.py   # Worker 协议实现与 JSONL IPC
│   │   └── service.py          # FIFO 任务队列、状态转换、取消/重试
│   ├── web/                     # Web 控制台（纯标准库 asyncio，127.0.0.1:8765）
│   │   ├── __init__.py
│   │   ├── server.py            # HTTP API + /outputs 预览 + 静态文件（no-cache）
│   │   └── static/              # Midjourney 风格三栏前端（零依赖手写 HTML/CSS/JS）
│   │       ├── index.html       # 三栏骨架 + 五模式 Tab + 参数面板（data-modes 路由）
│   │       ├── app.js           # 会话/队列/想象/V·U/视频/音乐 Tab 逻辑
│   │       └── style.css        # 深色 MJ 极简风（手写 utility，无框架）
│   ├── ui/                      # PySide6 界面层（MJ 深色三栏，与网页端几乎一模一样）
│   │   ├── __init__.py
│   │   ├── main_window.py      # 主窗口：会话侧栏 + 创作区 + 参数面板三栏；十页系统功能并入
│   │   ├── session_store.py    # 会话分组本机 JSON（与任务库同目录存放，不出本机）
│   │   ├── theme.py            # 跟随系统的深浅色主题 + 网页端同款 MJ 调色 token 样式表
│   │   ├── pages/              # 页面组件
│   │   │   ├── __init__.py
│   │   │   ├── creation_page.py   # 创作页：五模式 Tab + 会话流 + 想象/V·U/重试/取消/lightbox/工具内播放窗
│   │   │   ├── engine_status_page.py
│   │   │   ├── settings_page.py
│   │   │   └── task_list_page.py
│   │   └── widgets/            # MJ 风格组件：参数面板/composer/feed/产物卡/骨架/lightbox/播放窗/toast
│   └── utils/                   # 工具函数
│       └── config.py
├── tests/                       # 测试目录
│   ├── test_sana_plan.py        # 现役出图预算（32 倍数 / 单遍 1.10MP / 目标 4.20MP / 放大档元数据）与两端面板一致性对拍
│   ├── test_sana_worker_adapter.py # 现役 worker 参数白名单（拒绝 hires/init_image_path）与 metadata 回传
│   ├── test_memprobe.py         # 空闲物理内存探测与加载闸门
│   ├── test_sd15_plan.py        # （后备）SD1.5 预算与分块档位对拍，测的是未注册路径
│   ├── test_hires_tiling.py     # 分块网格、羽化掩膜、融合几何（SD1.5 高清重绘用，Sana 不走分块）
│   ├── test_sd_worker_adapter.py # （后备）SD1.5 worker 参数白名单与 metadata 回传
│   ├── test_prompt_bilingual.py # （遗留）中文桥接回归 9 条，只覆盖未注册的 sd15 链路
│   ├── test_phase1_foundation.py
│   ├── test_task_service.py
│   └── test_cross_process_safety.py  # 执行者锁/原子领取/5秒轮询接管专项测试
├── LICENSES.md                    # 模型许可台账（现役集成：Apache-2.0 / MIT / Gemma Terms of Use，DC-AE 上游待复核）
├── docs/                        # 技术文档
│   ├── ARCHITECTURE.md         # 架构提案与模块边界
│   ├── DEVELOPMENT_PLAN.md     # 阶段验收计划
│   ├── ENGINE_RESEARCH.md      # 引擎候选调研
│   ├── HARDWARE_COMPATIBILITY.md # 硬件兼容性说明
│   ├── LICENSE_AUDIT.md        # 许可证审计记录
│   └── MODULES.md              # 模块清单与接口文档（模块化复用入口）
├── scripts/                     # 部署与运维脚本
│   ├── run_app.ps1              # Windows PowerShell 启动桌面程序
│   ├── verify_env.py            # 环境自检（Python/venv/依赖）
│   ├── build_launcher.ps1       # .exe 启动器构建
│   ├── acceptance_image.bat/.ps1/.py # 现役文生图（Sana）真机验收：1024×1024 + 1920×1088 两档，逐档打印实测 peak_vram_mb，超 6000MB 立即停止；要求空闲显存 ≥6144MB 且空闲内存 ≥16000MB，不满足就每 180s 重试（最多 40 轮）
│   ├── acceptance_video.bat/.ps1/.py # Wan2.1 真机验收（文生视频 + 图转视频，自动等显存）
│   ├── acceptance_music.bat/.ps1 # ACE-Step 端到端验收（中文人声 48kHz wav + 时长截断实测）
│   ├── acceptance_chain.ps1     # 依序 image→video→music 链式自动验收（显存窗口打开后自动跑，日志落 logs\）
│   ├── release_local_engine.bat/.ps1  # 释放本机 LLM 引擎显存（生成前必跑）
│   ├── agent_llm.ps1            # 「AI 智能体」自带大模型：-Setup / -Start / -Stop / -Status（llama.cpp 与 GGUF 权重都在项目内，只绑 127.0.0.1:11435）
│   ├── run_agent_llm.bat        # 上面那个脚本的双击入口
│   ├── import_llm_model.ps1     # 【旧链路·保留不用】把机器上既有的 Ollama 模型库按 manifest 精确导回项目内（改用自带 llama.cpp + GGUF 后不再需要）
│   ├── run_web.ps1 / run_web.bat # 网页端启动器（-Restart / -Status / -Foreground / -Port / -NoBrowser）
│   ├── setup_music_venv.ps1     # 音乐引擎环境一键搭建（.venv-music，幂等）
│   ├── download_music_weights.py # ACE-Step 1.5 权重下载（断点续传）
│   ├── setup_video_venv.ps1      # 视频引擎环境一键搭建（.venv-video，幂等）
│   ├── download_video_weights.py # Wan2.1 T2V/VACE 权重下载（hf-mirror，断点续传）
│   ├── setup_image_venv.ps1      # 图像引擎环境一键搭建（.venv-image，幂等）
│   ├── download_sana_weights.py  # 现役 Sana-1.6B 多语言版权重下载（hf-mirror、只取 fp16 约 9.3GB、黑名单跳过 fp32 三件约 12.3GB、断点续传；只报缺失与目录体积，无逐文件 SHA-256）
│   ├── download_image_weights.py # （后备）SD1.5/RealVis 三件套下载与离线校验，现役不跑
│   ├── finalize_music_weights.py # 权重下载监控 + 同步/校验自动收尾
│   ├── probe_coerce.py          # 提示词规整（coerce_prompt_text）单元断言
│   ├── probe_task_db.py         # 任务库只读查询（排查历史失败任务）
│   ├── smoke_desktop_generation.py # 桌面端 MJ 同款创作界面冒烟（离屏 Qt，不启动 GPU worker）
│   ├── shot_desktop_ui.py       # 离屏验收截图：深色/浅色/系统页三张（.tmp_shots\）
│   ├── check_hardware.py        # 启动前置硬件检测（显存/内存分级，只读本机）
│   ├── fix_portable_paths.py    # 便携包首启路径自适应（纯本地文本替换）
│   ├── build_portable.ps1       # 一键便携打包脚本
│   └── portable/                # 打包模板：启动辰鹄.bat / 启动桌面端.bat / 打包与迁移说明.md
├── data/                        # 默认数据根：配置、会话数据库、日志、权重缓存、输出（可被环境变量覆盖）
├── .venv/                       # PySide6 桌面程序虚拟环境（Python 3.11）
├── .venv-image/                 # 图片引擎专用虚拟环境（现役跑 Sana-1.6B 多语言版 worker：torch 2.14.1+cu130、diffusers 0.41.0、transformers 5.19.0；SD1.5 后备代码也留在仓库里但不注册、不接线）
├── .venv-video/                 # 视频引擎专用虚拟环境（torch cu130 + diffusers WanPipeline/WanVACEPipeline）
├── .venv-music/                 # 音乐引擎专用虚拟环境（transformers<4.58，与 .venv-image 隔离）
├── requirements-dev.txt         # 开发依赖（含 pytest）
├── pytest.ini                     # pytest 配置（只收 tests，排除各 venv/dist/vendor）
└── requirements.txt             # 桌面 UI 依赖（仅 PySide6）
```

---

## 🖥️ 启动桌面程序

### 前提条件

- Windows 操作系统
- Python 3.11+（系统安装：当前检测到 Python 3.14.7）
- NVIDIA GPU（可选，需已安装驱动；本项目支持 CPU 推理但速度较慢）

### 快速启动

```powershell
# 使用项目启动脚本（推荐）
.\scripts\run_app.ps1
```

或手动启动：

```powershell
# 确保虚拟环境存在
py -3.11 -m venv .venv

# 激活虚拟环境
.\.venv\Scripts\activate

# 安装桌面依赖
python -m pip install -r requirements.txt

# 运行应用
python -m app.bootstrap
```

### 启动选项

| 选项 | 说明 |
|------|------|
| `--test-headless` | 无头模式（不显示 GUI，用于 CI/CD） |
| `CHENGUAI_DATA_ROOT=<path>` | 覆盖默认数据根目录（模型/输出目录）；**默认不再写死盘符，改为项目根下 `data\`**（便携包整目录复制即数据随行） |
| `CHENGUAI_APP_HOME=<path>` | 覆盖配置/会话数据库/日志所在的应用数据目录（便携包注入为包内 `data\apphome`） |
| `CHENGUAI_DISABLE_MODALITIES=image,video,music` | 逗号分隔的禁用模态：对应引擎直接不注册（启动硬件检测分级自动注入；手工设置可用于调试）。三个开关互不牵连——禁用 `image` 只是不注册 `sana-multiling` 与 `wan-i2i`，视频与音乐照常注册 |
| `CHENGUAI_THEME=auto\|light\|dark` | 界面主题覆盖；默认 `auto` 跟随 Windows 系统深浅色，运行中切换系统主题即时生效 |
| `CHENGUAI_DISABLE_WEB_AUTOSTART=1` | 本次启动不让桌面端带起网页控制台（等价于把 `web_autostart` 设为 `false`，只影响当次）；测试环境默认就是不带起 |

---

## 🌐 Web 控制台（Midjourney 风格 · 五模式 Tab）

浏览器版创作台，与桌面程序共用同一服务层与任务队列，地址 <http://127.0.0.1:8765>（仅本机）。

### 启动（推荐用启动器；网页端与桌面端必须同时可用）

```powershell
# 双击 scripts\run_web.bat，或在项目根目录执行：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1            # 没起就在后台起，已起就直接开浏览器
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Restart   # 改过 Python 代码后重启（只杀本项目的 web 进程）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Status    # 只看谁在监听端口
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_web.ps1 -Foreground # 前台运行，错误直接打在终端里
```

启动器从 `config.json` 读 `web_host` / `web_port`（默认 `127.0.0.1:8765`）：端口被别的程序占用时会说清楚是谁占着、该怎么办，不会把别人的服务当成自己，也不会甩 traceback。桌面端照常 `scripts\run_app.ps1`，两端共用同一条 SQLite FIFO 队列与执行者锁。

**只开桌面端也能够打开网页端**：桌面端启动时会顺手把网页控制台带起来（日志里写「网页端已随桌面端启动：http://127.0.0.1:8765（PID …）」）；端口上已经是自家的网页端在监听就不重复起（认 `/api/health` 里的自家标记，别人的服务不当成自己、也绝不代杀）。桌面端自己不监听任何端口，所以两端不互抢端口：真撞上端口被第三方占着，网页端会自己往后挪一格（`8766`…最多五格）继续跑，真实地址记在 `logs\web-console.port`，`run_web.ps1` 认这个地址（要关掉自动挪端口就把 `web_port_fallback` 设为 `false`）。桌面端退出时若还有任务在排队/执行就故意留着网页端，免得把正在跑的任务拦腰砍断。不想要自动带起就把 `config.json` 里的 `web_autostart` 改成 `false`（文件里没这个键就自己加一行；也可以用环境变量 `CHENGUAI_DISABLE_WEB_AUTOSTART=1` 只关掉本次）。以前「桌面端跑得好、网页端打不开」正是这个原因：Web 服务压根没起来，8765 上没人监听。

也可以手工起：

```powershell
.\.venv\Scripts\python.exe -m app.web.server
```

日志见 `logs\manual_console\console.out.log / console.err.log`。`static\` 目录实时读取，改前端**无需重启**；改 `app\web\server.py` / `app\agent\*` 需要 `-Restart`。

> 浏览器里请用 <http://127.0.0.1:8765/> 打开。服务只接受指向本机的 Host / Origin：`localhost`、`127.0.0.1`、`::1`、`127.x.x.x` 互为同源（本机代理改写 Host 时不会被误判成跨站——旧实现按字符串严格相等比较，会把正常页面整个判死，这就是「网页端突然没了」的成因）；真被 403 拦下时，服务日志会记下实际的 Host/Origin，403 正文里也写明该用哪个地址。浏览器走代理的话把 `127.0.0.1`/`localhost` 加进「不使用代理」列表。

### 界面结构（三栏）

- **左栏**：会话列表（新建/切换/双击标题或 ✎ 重命名/删除仅删本地分组）；
- **中栏**：对话流 + 底部 Imagine 输入（Enter 提交、Shift+Enter 换行、拖入/粘贴图片挂参考图）；
- **右栏**：参数面板（引擎、尺寸、强度、时长、steps、seed、负面词、风格、高级 JSON）。

### 顶部 Tab 路由（纯前端路由，后端接口不动；AI 智能体自 2026-11-06 起排第一位）

| Tab | 提交 | 专属参数 | 后端行为 |
|-----|------|----------|----------|
| **AI 智能体** | 一句话（生成/聊天/文案/朗读） | 垫图上传、能力 pill | `POST /api/agent/command` → 同一条 `TaskService` 队列 + 本机大模型/SAPI，见下文「第六板块」一节 |
| 文生图 | **单任务、一次 1 张**（`task_type=image`；2026-11-10 起 Sana 不做 4 变体批） | 尺寸（六档：1024×1024 / 1152×640 / 640×1152 / 1152×864 / 864×1152 / 1920×1088，自定义滑杆 **320-2048、步长 32**）/**steps（默认 20，允许 10-40）**/seed/负向词；CFG 固定 4.5 不给调 | **Sana-1.6B 多语言版**（`sana-multiling`，`.venv-image` 独立 worker）：中英文原生直出，无需关键词桥接；尺寸必须是 **32 的倍数**、单边 ≤2048；超单遍像素预算 1.10MP 自动「底图 + Lanczos 放大」，1080p 档即走此路（`upscaled=true`、`tiles=0`） |
| 图生图 | **单任务、一次 1 张** | 上述 + **垫图上传区**（**没有**「重绘强度」这个参数：改动幅度由提示词决定） | **Wan2.1-VACE-1.3B 参考重绘单帧**（`wan-i2i`，`.venv-video` 独立 worker，与视频共用 `models\wan2.1`，需本机绝对路径）：尺寸 **16 的倍数**、像素预算 459264（480p 档约 0.46MP，与视频、桌面端同一份数）、steps 默认 28 |
| 文生视频 | 单任务（`task_type=video`） | 视频尺寸/**时长（5–161 帧）**/steps | **Wan2.1-T2V-1.3B**（`wan-t2v`，`.venv-video` 独立 worker），16fps mp4，提交前显存预检 |
| 图生视频 | 单任务 | 同上 + **首帧图上传区** | **Wan2.1-VACE-1.3B**（`wan-i2v`）：首帧=垫图条件生成（mask 首帧 0、其余全生成），其余同上 |
| AI音乐 | 单任务（`task_type=music`），提交前前端预检显存并弹能力边界确认 | BPM/目标时长（30–480s）/曲风 | **ACE-Step 1.5**（`.venv-music` 独立 worker），48kHz wav |

要点：
- **一次想象：文生图 1 张**（2026-11-10 换 Sana 后不再 4 变体批——Sana 每张都要过一遍完整去噪，4 张会把队列时间拉到 4 倍而质量不变）；图片瓦片上仍保留 **V**（变体）与 **U**（放大）两个动作，按源图出自哪个引擎分流：Sana 源图不接受垫图——`U` 是同提示词同 seed 按更大尺寸重新出图（×1.34、32 的倍数、单边 ≤2048、目标 ≤4,200,000 像素，超单遍预算走底图+Lanczos 放大），`V` 是原尺寸换 seed 重新出图；`wan-i2i` 与历史 `wan-t2i` 的源图才走 `wan-i2i` 参考重绘（×1.34、16 的倍数、480p 档 459264 像素预算）。两者都一次 1 张，且图生图引擎没有「重绘强度」参数；
- **会话全局共用**：同一会话可混排图片网格、视频卡片（卡片内 `<video>` 直接播 + 引擎写出的首帧当 poster + `⛶`/双击在灯箱里放大播放 + ⬇下载）、音频播放条（`<audio controls>`，同样在卡片内播）；
- Tab 徽标 = 该模态活跃任务数。注意：各模式**前端提交与展示相互独立**，底层 TaskService 是**单一 FIFO 队列（串行执行）**，切 Tab 不会并发抢占；
- 视频参数（导出固定 **16fps**（Wan 原生）、帧数 `4n+1`（5–161）、尺寸 16 的倍数、默认 832×480）前端与引擎两侧同口径收口；提交前做显存/内存预检（引擎侧按 6144MB 显存档位设计，加载另看空闲物理内存；超 token 预算的组合在提交前直接拦下并说明两条出路，**不自动缩画幅**，旧的「空闲 <10500MB 直接拦截」口径已作废），引擎未注册时诚实拒绝，不产生任何任务与输出；
- 浏览器安全边界：无法读取剪贴板/拖入图片的真实磁盘路径，前端会**记住上次目录并按文件名预填**，用户核对即可；V/U 从已生成图走图生图不受此限；
- 生成前先跑 `scripts\release_local_engine.bat` 释放显存，否则大概率 OOM。

---

## 🎵 AI 音乐：ACE-Step 1.5 已接入（2026-11；权重已就位并校验通过【权重就绪】，真机首测待显存释放）

筛选硬条件：许可证合规优先（避免 NC）、RTX 3060 12GB 舒适、Windows + `.venv-image`（torch cu130）可部署、64GB 内存。

| 候选 | 权重许可 | 权重大小 | 12GB | 人声/时长 | 推荐 |
|---|---|---|---|---|---|
| **ACE-Step 1.5** | **MIT**（官方卡片） | AIO 9.6GB / turbo 组件约 9GB | ✅ 官方称优化后 <4GB | 人声+器乐，≤4–10min，BPM/调性/时长可控 | ★★★★★ **首选** |
| DiffRhythm2（小米/ASLP） | Apache-2.0（代码+权重） | 4.8GB | ✅ 轻松 | 人声整曲+歌词对齐 | ★★★★ 无-NC 次选 |
| DiffRhythm 1.x | DiT Apache-2.0；**VAE 为 Stability 社区许可** | 2.7GB | ✅（最低 8GB，chunked） | 人声 ~90s | ★★★ |
| stable-audio-open-1.0 | Stability 社区许可（个人本地免费；商用 <$1M 营收线） | 5.2GB | ✅ | 仅器乐 ≤47s | ★★★ 器乐补充位 |
| MiniMax-Music3 | MiniMax 社区许可（无 NC；$20M 营收线 + UI 标注义务） | ~34GB | ⚠️ offload 可跑、慢 | 人声 ≤5min 质量高 | ★★★ 需 ComfyUI/GGUF 路线 |
| facebook/musicgen-* | **CC-BY-NC-4.0（权重）** | 2.3–13.1GB | small 可行 | **无人声**，≤30s | ✗ 违反无-NC |
| YuE v1 7B / YuE2-3B | Apache-2.0 / **CC-BY-NC-4.0** | 13.8GB / 6.9GB | ❌（需 24GB） | 人声 | ✗ 显存或许可不合格 |
| MusicLDM / JEN-1 / SmallAudioLoRA / DanceDiffusion | NC-SA / 无官方权重 | — | — | — | ✗ 剔除 |

**结论：首选 ACE-Step 1.5（turbo 配置）**——唯一「MIT 权重 + 真人声 + 高质量 + 轻量 + Windows 官方支持」四项全占；官方 org 为 `ACE-Step`（不存在 facebook/ACE-Step）。次选 DiffRhythm2（最严格无-NC 场景）。MusicGen 等 NC 候选在「许可证合规优先」原则下一票否决。

### ✅ 接入实施状态（已落地，真机端到端验收待做）

- **独立环境 `.venv-music`**（不动 `.venv-image`——ACE-Step 官方钉 transformers `<4.58` 与 `.venv-image` 的 v5.x 硬冲突）：一键脚本 `scripts\setup_music_venv.ps1`（幂等）。要点：PyTorch **2.14.1+cu130 三件套**先装依赖、后 `--force-reinstall --no-deps` 强制复位（依赖批次会把 torch 降级成 PyPI CPU 版）；`pip install -e vendor\ACE-Step-1.5 --no-deps`（PyPI 上的 `ace-step` 只有 v1，必须源码安装）。**不需要 triton-windows**（repo 自带无-triton 回退）。
- **权重（MIT，全本地离线）**：`scripts\download_music_weights.py` 下载 HF `ACE-Step/Ace-Step1.5` 最小集（turbo DiT + `acestep-5Hz-lm-1.7B` + `Qwen3-Embedding-0.6B` + `vae` + config，约 **10.08GB**）到 `models\ace-step-1.5`。⚠ 下载完成后需把 `vendor\ACE-Step-1.5\acestep\models\turbo\*.py` 复制进 checkpoint 目录（等价官方 `_sync_model_code_files`）。
- **注册与 worker**：`app\engine_registration.py` 注册 `ace-step-15`（`music` 模态；`.venv-music` 或 `vendor\ACE-Step-1.5` 缺失时自动跳过注册，前端如实提示）；推理在独立子进程 `app\tasks\music_worker.py`，图片/视频模块代码**零改动**。
- **能力边界（提交前弹窗确认）**：支持 4–10 分钟带人声完整乐曲（人声+器乐）或纯器乐；**不支持超长多轨道复杂编曲**。12GB 显存档位（带 LM）实际最长约 8 分钟：worker 将目标时长 clamp 到 480s 并用 soundfile 读回真实时长。BPM 30–300 显式传参生效。
- **显存门槛 12GB（前端直接拦截，不向后端抛任务）**：`imagineMusic` 提交前查 `/api/hardware`（nvidia-smi + 内存快照，新增只读端点）：检测不到 NVIDIA 显卡 / 总显存 <12288MB / 空闲 <7000MB → toast 拦截，文案含「显存不足，无法启动ACE-Step1.5音乐模型」；空闲不足时提示先跑 `scripts\release_local_engine.bat`。
- **队列**：与图片/视频共用**同一 FIFO 串行队列**（TaskService 单 worker 调度），绝不并发抢占显存。产物 **48kHz wav** 写入本地会话记录（统一存储），页面内置 `<audio>` 播放器 + ⬇下载按钮。
- **进程轮换**：上游在 Windows 连续多次生成后有 c10.dll 崩溃报告（issue #1338，未修复）——worker 服务满 `music_max_tasks_per_worker`（默认 4）个任务后在任务收尾空闲时自退，下一个任务由进程适配器透明重建（代价：重载权重约 1–2 分钟），零任务丢失。`offload_dit_to_cpu=True` 必开（否则 VAE decode 回落 CPU，20 秒音频要解 26+ 分钟，issue #1337）。
- **可配置项（config.json，便于打包迁移）**：`music_venv_python`、`music_model_dir`（默认项目内 `models\ace-step-1.5`）、`music_repo_dir`（默认 `vendor\ACE-Step-1.5`）、`music_lm_model`、`music_max_tasks_per_worker`。环境变量全相对项目路径注入（`CHENGUAI_MUSIC_*` + `ACESTEP_CHECKPOINTS_DIR` 等），无任何云端 API / 在线校验 / 远程上报。
- **取舍说明**：worker IPC 协议无中间进度回调通道，「加载权重 → LM 谱曲 → DiT 去噪 → VAE 解码编码」三阶段以运行卡片上的管线说明 + 队列状态（QUEUED/PREPARING/RUNNING）表达，非逐阶段实时推送。

关键来源：ACE-Step1.5 卡片 <https://huggingface.co/ACE-Step/Ace-Step1.5>；DiffRhythm2 <https://github.com/xiaomi-research/diffrhythm2>；stable-audio-open 许可 <https://huggingface.co/stabilityai/stable-audio-open-1.0/blob/main/LICENSE.md>；MiniMax-Music3 许可 <https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE>；musicgen 卡片 <https://huggingface.co/facebook/musicgen-small>。（本机直连 HF 超时，核实走 hf-mirror.com API 与 GitHub 原文）

### ✅ 权重下载收尾（2026-11-05 完成【权重就绪】）

- 断点续传完成：`models\ace-step-1.5` 下四件 safetensors **全部落盘并逐文件校验通过**（`safetensors` 解析文件头 + 张量计数）：
  `acestep-v15-turbo\model.safetensors` 4,787,825,604 B / 677 tensors；`acestep-5Hz-lm-1.7B\model.safetensors` 3,708,521,528 B / 310 tensors；`Qwen3-Embedding-0.6B\model.safetensors` 1,191,586,416 B / 310 tensors；`vae\diffusion_pytorch_model.safetensors` 337,431,388 B / 365 tensors——合计 **10.03 GB**。
- 建模代码已同步：`vendor\ACE-Step-1.5\acestep\models\turbo\configuration_acestep_v15.py` 与 `modeling_acestep_v15_turbo.py` 已 copy2 进 `models\ace-step-1.5\acestep-v15-turbo\`（等价官方 `_sync_model_code_files`）。
- 一键复核：`python scripts\finalize_music_weights.py`（监视下载 + 自动收尾）或 `--verify-only`（直接同步 + 校验四文件）。校验通过打印 **[权重就绪]**；在此之前音乐生成功能不可用属预期（引擎 health_check 拒绝启动）。
- 🧹 待批准清理：`models\ace-step-1.5\.cache\huggingface\download\` 内残留约 **3.9 GB** 旧会话断点垃圾文件（下载已完成，这些 `.incomplete` 不再被任何流程引用；便携打包脚本已排除 `.cache`）。删除操作按约定需用户点头后执行。
- ⚠ 真机首测仍需：先双击 `scripts\release_local_engine.bat` 释放显存，再从 Web 控制台「AI音乐」Tab 提交中文人声曲目端到端验收。

---

## 🖼️ 文生图引擎 Sana-1.6B 多语言版（2026-11-10 接入：`sana-multiling`，一次 1 张，真机峰值未实测）

**这一节是现役文生图的唯一口径**，之前所有写 FLUX 或 SD1.5/RealVis 的章节都是历史记录。

- **模型与许可**：`Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers`，Apache-2.0、`gated=false`（本会话按 HF API 核验，快照内含 LICENSE，11358 字节）。文本编码器是 **Gemma-2-2B-IT**，按 **Gemma Terms of Use**（允许商用，再分发要按条款附上声明）；VAE 是 **DC-AE 32×**，上游仓库 `mit-han-lab/dc-ae-f32c32-sana-1.0-diffusers` 的许可标注**尚未复核**，发布前必须补上，见 `docs/LICENSE_AUDIT.md` 文末「2026-11-10 增补」。
- **权重**：`models\sana-multiling`，磁盘实测 **9.06 GB**（下载脚本口径约 9.3 GB，只取 fp16 三件；fp32 三件约 12.3 GB 用黑名单跳过）。缺权重时用 `scripts\download_sana_weights.py`（hf-mirror、断点续传）；脚本只打印缺失清单与目录体积，**没有逐文件 SHA-256 校验**，这是已知缺口。健康检查失败会点名缺哪个模块。
- **为什么换掉 `wan-t2i`**：Wan2.1-T2V-1.3B 单帧出图的像素上限被视频档锁死在 480p（约 0.46 MP），画幅想大一点只能牺牲视频；Sana 的预算独立，且原生中英文、Apache-2.0。旧 `wan-t2i` 任务的历史记录仍可读，不删数据。
- **出图口径**（唯一真相源 `app/engine/image/plan.py` 的 `SANA_PROFILE`）：尺寸必须是 **32 的倍数**（DC-AE 是 32× 下采样，倍数不对会静默漂移）、单边 ≤**2048**、单遍像素预算 **1,100,000**、目标硬上限 **4,200,000**。界面滑杆 **320-2048、步长 32**；默认 **1024×1024**、**steps 20**（允许 10-40）、**CFG 固定 4.5**（多语言版官方推荐值，不给调，调错会崩图）。档位：1024×1024 / 1152×640 / 640×1152 / 1152×864 / 864×1152 / **1920×1088（放大档）**。超过单遍预算时按预算出底图再 **Lanczos 放大**到目标尺寸，产物元数据写 `upscaled=true`、`tiles=0`——**不谎称原生 1080p，也没有分块重绘**。
- **显存策略与闸门**（`app/engine/image/sana.py`）：fp16 三件**逐模块换入**——编码器 4757.0+229.5 MB（最大单件 **4.99 GB**）、transformer **3060.3 MB**、VAE **1191.2 MB**，任一时刻显存里只有当前一件，所以**推算**峰值能压在 6 GB 内。**这个 6 GB 是推算值，本机尚未实测**（显存长期被常驻大模型占满）。两道闸门：空闲显存 <**5120 MB** 自动退到「逐层换入」（慢约两三倍）、<**1500 MB** 直接拒绝并点名是谁占着显存；加载权重另要求空闲物理内存 **≥16 GB**（`app/engine/memprobe.py`，起因是 2026-11-08 Wan 在内存不足时被系统静默杀掉）。排障环境变量：`CHENGUAI_SANA_MIN_FREE_MB`、`CHENGUAI_SANA_MIN_FREE_RAM_MB`、`CHENGUAI_SANA_GUIDANCE`。
- **验收**：`scripts\acceptance_image.bat`（→ `.ps1` → `.py`）要求空闲显存 ≥**6144 MB** 且空闲内存 ≥**16000 MB**，跑 1024×1024 与 1920×1088 两档，逐档打印实测 `peak_vram_mb`，**超过 6000 MB 立即停止**；不满足条件时每 180 秒重试、最多 40 轮。跑之前先双击 `scripts\release_local_engine.bat`。产物文件名 `sana_txt2img_*.png` / `sana_txt2img_upscaled_*.png`。
- **一次 1 张**：Sana 每张都要走完整去噪，与视频、音乐共用同一条 FIFO 串行队列，界面照实显示排队位置。

## 🎬 视频引擎 Wan2.1（2026-11-05 接入：`wan-t2v` 文生视频 / `wan-i2v` 图生视频）（历史接入记录：本节里的「bf16 + 模型级 offload、T2V 换入峰值约 10.6GB、12GB 档」是当时的估算；2026-11-08 已改成**逐层 CPU 卸载**，真机实测文生视频峰值 **2021MB**、门槛降到 6144MB，见文末「FLUX 出局」章节）

**选型（用户确认）**：Wan2.1 官方**不存在 I2V-1.3B 档**（I2V 只有 14B）；1.3B 档图生视频唯一官方
Apache-2.0 形态是 **VACE**。据此采用：

- 文生视频：HF `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` → 引擎 `wan-t2v`（diffusers `WanPipeline`）；
- 图生视频：HF `Wan-AI/Wan2.1-VACE-1.3B-diffusers` → 引擎 `wan-i2v`（`WanVACEPipeline`，
  首帧=垫图作为条件（`prepare_video_and_mask`：首帧 mask=0、其余帧待生成），非「FLUX 先出帧再渲染」）。

- **隔离环境 `.venv-video`**（不动 .venv-image/.venv-music）：一键脚本 `scripts\setup_video_venv.ps1`（幂等）：
  PyTorch **2.14.1+cu130 三件套** + diffusers 0.41.0 / transformers 5.19.0 / accelerate 1.15.0 / safetensors /
  huggingface_hub 1.33.0 / sentencepiece / imageio(-ffmpeg)，装完做 `WanPipeline`/`WanVACEPipeline` 导入验证。
- **权重（Apache-2.0，经 hf-mirror 断点续传下载）**：`python scripts\download_video_weights.py` 把两快照
  下到 `models\wan2.1`（HF 新缓存布局：共享 `blobs\`，快照目录为链接）。**本机实测物理合计 44.18 GB**
  （T2V text_encoder 21.16GB + VACE text_encoder 10.58GB 分片哈希不同不共享；VAE blob 共享。
  脚本 `_dir_size` 会跟随链接双计，勿以 133GB 报告值为准）。
- **引擎与 worker**：`app\engine\video\wan.py`——fp32 VAE + bf16 pipeline + `enable_model_cpu_offload` +
  UniPC `flow_shift=3.0`（480p 档）；尺寸取 16 的倍数并按像素预算（≤832×480×1.15）自动收口；帧数 `4n+1`、
  **16fps**；OOM 捕获后诚实报错并指引 `release_local_engine.bat`，绝不伪造。worker `app\tasks\video_worker.py`
  参数白名单 `width/height/num_frames/steps/seed/negative_prompt`（i2v 另加 `init_image_path`；
  t2v 带垫图直接报错「请改用图生视频引擎」）。
- **模式 ↔ 引擎一一对应**：两端 `MODE_ENGINE_ALLOW`（t2v→`wan-t2v`、i2v→`wan-i2v`）自动过滤引擎下拉与
  提交解析，不会把 i2v 任务投给 t2v 引擎。
- **显存预检（前端直接拦截）**：总显存 <12288MB 或空闲 <10500MB → toast 拦截（Wan2.1 UMT5-XXL 编码器
  bf16 换入峰值 T2V 约 10.6GB / VACE 约 5.3GB，加载期内存占用高），指引先双击 `scripts\release_local_engine.bat`。
- **产物**：`wan_{t2v|i2v}_{时间戳}.mp4`（16fps）写入输出目录；前端视频卡片播放 + ⬇下载。
  12GB 档 81 帧 480p（≈5.1 秒）预计数分钟到数十分钟，首次含权重加载；**真实耗时与显存峰值以真机实测为准**（见验收记录）。
- **许可**：两仓库 Apache-2.0（HF API 实测 gated:false），台账见根目录 `LICENSES.md` 与
  `docs\LICENSE_AUDIT.md`「模型许可台账」。

---

## 🔧 FLUX 稳定性修复（2026-11-05）：中英文提示词 + 输出目录（历史：FLUX 已于 2026-11-08 删除，先换 SD1.5 + RealVis，**SD1.5 也已于 2026-11-10 下架**；现役文生图是 Sana-1.6B 多语言版，见上面「文生图引擎 Sana-1.6B 多语言版」一节）

**症状**：Web 控制台一批文生图全部报 `TextEncodeInput must be Union[TextInputSequence, Tuple[InputSequence, InputSequence]]`；任务卡片同时出现「产物不在输出目录」。

**根因**（均经脚本实测定位，诊断工具保留在 `scripts\`）：
1. `TextEncodeInput` 是 tokenizers（Rust 后端）对**输入类型**的严格校验：只有**嵌套 list**（如 `[["中文…"]]`）会触发该报错——`scripts\probe_flux_tokenizer.py` 用本地 FLUX 快照 tokenizer 离线复现，纯中文/纯英文/中英混合的 `str` 与 `List[str]` 全部正常（CLIP (1,77)、T5 (1,512) 形状正确），bytes 报的是另一种错。失败批次经 `scripts\probe_task_db.py` 只读核查，入库 prompt 均为合法 str；报错来自**旧 worker 进程遗留行为**（无 git 历史无法追溯当时代码），故按「防御收口」原则在所有入口统一规整。
2. 「产物不在输出目录」：Web 侧此前用 `AppPaths.output_dir`（数据根迁移后指向项目 `data\Output`），而 worker 按 `CHENGUAI_OUTPUT_DIR`（config 存值 `D:\ChenguAI\Output`）写盘，**两侧目录不同源**，产物 URL 映射判空。

**修复**（未动任何画质参数：尺寸/steps/采样器/CFG/guidance/重绘强度/调度器零改动）：
- 新增 `app\engine\prompt_utils.py`：`coerce_prompt_text()` 把任意来源（str/bytes/任意嵌套 list/tuple/标量混排）的提示词统一规整为 FLUX 合法的纯 `str`（深度展平、UTF-8 解码、过滤空片段、逗号连接）；中文/英文/混合均原生支持。三层收口：Web 提交校验（本就要求 str）→ `app\tasks\flux_worker.py` 的 `generate()` 入口 → `app\engine\image\flux.py` 的 `generate_image()` 入口（空提示词直接报「提示词为空：请输入中文或英文描述后重试」，不再抛底层 TypeError）。
- **输出目录先行创建**：`flux.generate_image()` 在加载模型/开始生成**之前**完成输出目录解析与创建（`expanduser` + 相对路径绝对化 + `resolve`，Windows 反斜杠/转义统一处理；创建失败报「无法创建输出目录」明确文案）。
- **两侧同源**：`app\web\server.py` 的 `WebApplication` 现优先取 config 的 `output_dir`（与 worker 的 `CHENGUAI_OUTPUT_DIR` 同一来源），并在控制台启动时自动建目录；`/outputs/<file>` 预览映射恢复（实测 HTTP 200 `image/png`，历史 4 张成功图可正常预览）。
- 验证：`py_compile` 全绿；`scripts\probe_coerce.py` 单元断言全过；重启 Web 控制台后 `GET /api/tasks` 里 completed 任务的产物 URL 恢复非空。中文提示词端到端出图验收待显存释放后执行 `scripts\acceptance_flux.bat`。

---

## 🖥️ 桌面端三模态生成接线 + 共享任务服务跨进程安全（2026-11-05 · 历史：界面已被下一节 MJ 同款界面取代；跨进程安全与共享服务层仍然有效）

桌面端（PySide6）的 AI 图片 / AI 视频 / AI 音乐 三个页面已从「预览占位」升级为**真实提交入口**，与 Web 模式共用完全相同的服务层（同一 `TaskService`、同一 SQLite FIFO 队列、同一引擎 worker 子进程协议），无任何重复实现：

- **`app\ui\pages\workspace_page.py`**：按模态渲染表单——图像（描述即可）；视频（描述 + 可选首帧图片路径，留空由引擎按描述生成首帧）；音乐（描述 + 歌词，支持中文，歌词留空自动按纯器乐生成；表单侧校验与 worker 参数白名单一致，如歌词 ≤4096 字符）。引擎注册（权重与依赖在注册时校验通过）后「生成」按钮启用，点击即 `TaskService.submit_task()` 落库排队，结果状态诚实回显（提交失败直接报错，不兜底）。
- **`app\ui\main_window.py`**：新增 `MUSIC_ENGINE_ID`（`ace-step-15`）映射——音乐页引擎状态、标签（ACE-Step 1.5（音乐））、页面描述（权重 10.03 GB 已校验、真实 48kHz wav、12GB 显存档自动限长 8 分钟）、首页模型行全部对齐真实状态；旧「尚未安装/验证」文案只保留在真正未注册的场景。`_submit_generation()` 是三模态唯一的提交入口。
- **`app\bootstrap.py`**：桌面端在后台线程启动 asyncio 事件循环驱动 `task_service.start()`——任务执行逻辑与 Web 控制台一字不差。
- **跨进程安全（`app\tasks\service.py`，桌面端 + Web 控制台可同时开着）**：
  - **执行者锁**：数据库旁的 `.worker.lock` 文件锁（OS 自动释放，崩溃安全）；只有先持锁的进程运行任务 worker 循环并执行中断恢复，另一进程自动降级为「只提交/查看」，不会把对方正在运行的任务误标失败。
  - **原子领取**：任务用 `UPDATE ... WHERE status='queued'` 条件更新领取（`_claim_task`），两个进程绝无可能重复执行同一任务。
  - **5 秒接管**：worker 循环的等待改为 5 秒超时轮询，另一进程排入队列的任务最迟 5 秒被当前执行者领走。
- **验证（全部真实运行，非推演）**：`py_compile` 全绿；`pytest tests` **28 passed, 1 skipped**（含 `create_application` 离屏集成测试，覆盖新的后台事件循环）；`scripts\smoke_desktop_generation.py` 离屏冒烟 **SMOKE PASS**——三页 `ready=True`、按钮启用、标签正确；image/music/video 三条提交路径全部落库 `queued`（临时任务库，不启动任何 GPU worker）；空描述与超长歌词被诚实拒绝。

---

## 🎨 桌面端 MJ 同款创作界面（2026-11-05）（历史：本节里的引擎 id `flux-schnell`、「一次落库 4 个任务」都是当时的记录，图像引擎**已于 2026-11-10 下架换成 Sana、文生图改一次 1 张**；三栏结构与五 Tab 路由仍然有效）

桌面端从「十个侧栏页 + 生成表单」重构为与 Web 控制台**一模一样的 Midjourney 风格三栏创作界面**；`app/tasks`、`app/engine`、`app/web` 与共享服务层一行未改，`app/ui/` 之外只动了 `app/bootstrap.py` 两处。

### 布局与视觉

| 区域 | 内容 |
| --- | --- |
| 左栏（250px） | 品牌区 + 引擎状态副标题、`＋ 新建会话`、会话列表（缩略图 / 标题 / ✎ 重命名 / 🗑 删除，删除仅删本机会话分组，服务器任务与产物不受影响）、`系统功能` 七个入口 |
| 中栏 | 顶栏（会话标题 + 引擎药丸 + 任务服务健康药丸 + `⚙`）、**五模式 Tab**（文生图 / 图生图 / 文生视频 / 图生视频 / AI 音乐，Tab 右上角蓝色角标 = 该模态活动任务数）、会话流（批次气泡 + kind/mode/时间标签 + 产物网格）、composer（垫图区 + 多行提示词 + `想象 Enter` + 参数摘要行） |
| 右栏（320px） | 引擎选择与「重启」、按模式显隐的参数分区：尺寸 chips + 滑杆 + 数字框、steps（图 ≤12 / 视频 ≤50）、seed 锁定与随机、负向提示词、风格 / 曲风 chips（改写提示词）、图生图强度、视频尺寸与帧数、音乐 BPM 与时长、高级参数 JSON 白名单 |

配色直接沿用网页端 `style.css` 的 token（`--bg:#0a0b0d`、`--bg-soft:#101114`、`--bg-card:#16181c`、`--line:#23262c`、`--text:#e8eaed`、`--accent:#d8dade`、`--blue:#7d9bff`、`--red:#f07171`、`--green:#6fd08c`、`--radius:12px`），并继续跟随系统深/浅色（`CHENGUAI_THEME=light|dark` 可强制，4 秒轮询热切换）。

### 与网页端逐字对齐的行为（`app/ui/pages/creation_page.py`）

- **提交**：一次「想象」排 **1 个任务**（2026-11-10 文生图换成 Sana 后不再有 4 变体批——Sana 每张都要走一遍完整去噪，4 张只会把队列时间拉到四倍而质量不变）；面板勾了「固定 seed」就用面板里的 seed，否则每次随机；提交后立即清空 composer 并滚到底部。
- **门槛与文案**（与 `app.js` 逐字一致）：空提示词「先写提示词」；缺引擎的文案按板块点名——文生图缺 `.venv-image` 与 `models\sana-multiling`，图生图与视频共用 `.venv-video`；`i2i`/`i2v` 缺垫图各有专属文案；垫图必须是盘符开头的绝对路径，例：`D:\ChenguAI\uploads\photo.png`。
- **视频**：参数面板与快照逻辑（`num_frames` 按 `4n+1` 吸附，**16fps**，5–161；垫图存在时写 `init_image_path`）；引擎按模式一一对应（t2v→`wan-t2v`、i2v→`wan-i2v`，`MODE_ENGINE_ALLOW` 过滤）；提交前做显存预检：没有 nvidia-smi、或总显存 < **6144MB**（6GB 档）、或空闲低于 `VIDEO_FREE_FLOOR_MB`（2000MB）→ toast 拦截并提示双击 `scripts\release_local_engine.bat`；nvidia-smi 还在后台跑、暂时探不到显存时先放行，排队后引擎按真实显存判定，不够会明确报错、不会伪造输出。
- **音乐**：门槛顺序与文案逐字——引擎未注册 / 没有 nvidia-smi / 总显存 < **6144 MB**（6GB 档）/ 空闲低于 `MUSIC_FREE_FLOOR_MB`（6144 MB，提示双击 `scripts\release_local_engine.bat`）；通过后弹「ACE-Step 1.5 能力边界确认」：带人声（中英文）或纯器乐、时长 10-480 秒、输出 48kHz wav、按 6GB 档设计（LM/DiT 走 CPU 卸载，峰值显存为预期值、尚未实测，验收以元数据 `peak_vram_mb` 为准）、不支持超长多轨道复杂编曲（无分轨导出）。
- **V / U（图片卡片动作，按源图出自哪个引擎分流，两端同口径）**：源图来自 `sana-multiling` → Sana 不接受垫图，`U` 用同提示词、同 seed 按更大目标尺寸重新出图（尺寸 ×1.34 后按 32 的倍数对齐，超 4,200,000 像素目标上限等比回缩；超过 1,100,000 单遍预算时引擎按预算出底图再 Lanczos 放大到目标尺寸，元数据 `upscaled=true`），`V` 按原尺寸换 seed 重新出图（seed 规则两端一致：源任务有整数 seed 就取「源 seed + 1」，没有就发新的随机 seed，绝不沿用源 seed——沿用等于把同一张图重排一遍）；源图来自 `wan-i2i`（含 2026-11-10 前的`wan-t2i` 历史记录）→ 拿源图当垫图走 `wan-i2i` 参考重绘（16 的倍数、480p 档像素预算 459264 像素），`V` 同尺寸换 seed（同一 seed 规则）。各一次 1 张；图生图引擎没有「重绘强度」这个参数。失败任务「重试」走 `TaskService.retry_task`，命中原批次则并入，否则按参数回退模式建新批次；取消与引擎重启通过 `asyncio.run_coroutine_threadsafe` 回投到服务事件循环，结果用 toast 回执（跨线程经类信号，不用 `QTimer.singleShot`）。
- **产物展示**：一批现在只有 1 个任务，瓦片一律单列（`.grid-one`；CSS 里的四列网格只为历史批次保留）、悬停淡入 `V 变化`/`U 放大`、`seed x · W×H` 角注、lightbox 大图（←/→、V/U、复制提示词、打开所在文件夹）、视频卡片（引擎写出的首帧封面 + 「▶ 播放」开**工具内播放窗**：播放/暂停、进度条、音量、另存、打开所在文件夹，关窗即释放解码器；一次只留一个播放窗）、音乐内嵌播放条（进度、时长；放不出来时卡片上写明原因，之后才走系统播放器）、加载骨架（取消按钮 + 排队位次 + 管线说明）、失败卡片（重试 / 「已重试 →」）、toast 三态。
- **会话与状态**：`app/ui/session_store.py` 存 `desktop_sessions.json`（与任务库同目录，即 APP_HOME；临时文件 + `os.replace` 原子写，上限 120 会话），与 Web 的 localStorage 等价但换成本机文件；两端同开时读写同一份任务库，谁先持执行者锁谁执行。每 2.5 秒轮询 `list_tasks(limit=400)` 与 `list_statuses()`，用「状态签名」做脏检查后才重建网格。

### 十页功能一个不少

`首页概览 / 任务队列 / 生成历史 / 模型管理 / 引擎管理 / 系统设置 / 日志与诊断` 七页保留在左栏 `系统功能`，进入时带「← 返回创作」顶栏（`SystemPageShell`）并隐藏右栏参数面板；`app/ui/pages/task_list_page.py`、`engine_status_page.py`、`settings_page.py` 原样复用，`workspace_page.py` 已删除。`app/bootstrap.py` 的改动只有两处：`start_task_service_loop` 返回 `(stop, loop)`、把 `service_loop` 传给 `MainWindow`（`MainWindow(config_store, logger, database_path, task_service=None, engine_manager=None, service_loop=None)` 保持旧调用兼容）。

### 真实验证

`pytest tests` **31 passed, 1 skipped**（导航测试已重写为「五 Tab + 七系统页 + 会话新建」断言）；`scripts\smoke_desktop_generation.py` 离屏冒烟 **SMOKE PASS**：五模式顺序为 `t2i/i2i/t2v/i2v/music` 且切换生效、想象真实落库 4 个 `queued`（seed 互不相同、引擎 `flux-schnell`）、`i2i`/`i2v` 无垫图被诚实拒绝、`t2v` 参数快照 `frames=33 steps=28` 且无 `init_image_path`、音乐因空闲显存不足被门槛拦截（未弹窗、未伪造、任务数不变）、空提示词被拒、对 `queued` 任务重试诚实拒绝、无事件循环时取消报错不崩溃、会话文件已落盘。`scripts\shot_desktop_ui.py` 生成 `.tmp_shots\desktop_dark.png` / `desktop_light.png` / `desktop_system.png` 三张离屏截图用于核对三栏结构、4 格网格、骨架屏与系统页返回顶栏；注意离屏平台无中文字体，截图里中文显示为方框属平台限制，与样式表无关（Windows 实机正常）。**未启动任何 GPU worker，未写入真实任务库。**

---

## 🔧 中文出图与垫图校验修复清单（2026-11-05 傍晚）

### A. 引擎环境统一与中文端到端验收排队

- 排查「FLUX worker 为何用系统 Python」：根因是**旧代码时代启动的遗留进程**，现行 `register_production_engines` 在 `image_venv_python` 为空时本来就回退项目 `.venv-image`；已确认配置文件干净。本轮加硬保险：`image_venv_python`/`music_venv_python` 配置值指向不存在的解释器时**告警并回退项目默认 venv**，杜绝再次漂到系统 Python。旧 web server/worker 进程已停止并确认端口空闲。
- 中文端到端用例已进脚本：`acceptance_flux.ps1` Step 2b 用「一只橘猫趴在窗台上晒太阳，午后暖色阳光，写实摄影风格，毛发细节清晰，浅景深，8k，高清」@1216×704（steps=4，seed=20261105），验证 `coerce_prompt_text` 修复后不再出 `TextEncodeInput`。
- 新增 `scripts\acceptance_chain.ps1`：FLUX→音乐链式自动验收，每轮预检（需 ≥12.3GB 空闲显存 + ≥36GB 空闲内存）不满足就诚实等待（每轮 2h、最多 48 轮），日志落 `logs\acceptance_flux_rN.log` / `acceptance_music_rN.log`。当前本机常驻引擎占用约 11GB 显存 + 42GB 内存，**验收链已挂起自动重挂中；请双击 `scripts\release_local_engine.bat` 释放并保持本地聊天应用关闭**，窗口一开就跑，出图成功与否如实记录。

### B. 垫图必须是「已存在的具体图片文件」（三层拦截，杜绝把文件夹当垫图）

- **桌面端**（`creation_page._ref_path_error`）：composer 挂载路径与高级参数白名单里的 `init_image_path` 两处校验——目录报「垫图请选择具体图片文件，不能是文件夹」，不存在报「垫图文件不存在…」，后缀不支持报支持格式清单；高级参数命中报错时剔除该键并 toast，其余参数照常提交。
- **网页前端**（`app.js refShapeError`）：先行拦截目录尾巴（`\` `/` 结尾）与不支持扩展名；后端不可知「是否存在」，交给服务端。
- **Web 后端**（`app/web/server.py _submit_task`）：统一预检（非空字符串 → 盘符绝对路径 → 非目录 → 文件存在 → 后缀 png/jpg/jpeg/webp/bmp），400 + 中文提示，通过后归一化路径；引擎侧「输入文件不存在」保留为最后防线。
- 顺带修复：`composer.has_ref()` 原用 `isVisible()`，窗口未显示时会把已挂载垫图误判为未挂，已改 `isHidden()` 语义。
- 网页高级参数 hint 与垫图路径输入框 placeholder 均已注明「具体图片文件，不能是文件夹」。

### C. 音乐页 ready 文案

ACE-Step 1.5 已注册但未懒启动时，参数面板不再显示含糊的「尚未启动」，改为「ACE-Step 1.5（音乐）已注册 · 提交后懒启动（首次加载权重约 10.03 GB，最低 12GB 显存）」。

### D. 收尾与验证

- `.gitignore` 修正 `dist/` 与 `.tmp_shots/` 粘连成一条的笔误；`.venv-music/`、`vendor/` 已在忽略清单。
- `docs/` 七份文档全部同步到真实进度：架构增补（环境回退、垫图校验、MJ 界面）、MODULES 重写 app/ui 章节、开发计划阻断项改为现状+历史双层、许可审计增补集成后状态并清理旧草稿残留、硬件兼容标注 FLUX 已实测、调研摘要标注现状。
- 验证：桌面冒烟**新增断言后 SMOKE PASS**（目录垫图拦截 / 不存在路径拦截 / 有效 png 放行 4 连发含 `init_image_path` / 高级参数目录被剔除）、`pytest` **31 passed + 1 skipped**、`node --check app.js` 通过。
- 本轮 4 个本地 commit（`fix(refs)` 垫图校验、`fix(engines)` 环境回退+音乐文案、`test(scripts)` 验收与冒烟断言、`docs` 全套同步），未 push；push 与转公开仍等你决定。

---

## 📦 全模块本地离线便携打包（第三段 · 已实装；真实完整打包已执行，随开发增量同步）

目标：**文生图/图生图/文生视频/图生视频/AI音乐全部推理本地离线运行**（仅首次下载权重联网），
一键打包出完整目录包，复制到其他 Windows 电脑直接双击启动；不引入任何在线校验、远程上报、云端依赖。

### 一键打包：`scripts\build_portable.ps1`

```powershell
> 便携安装包体积：现役模型权重（2026-11-10 实测 `models\`）合计 **67.32 GB**——Wan2.1 44.18 + Sana-1.6B 多语言 9.06 + ACE-Step 1.5 9.40 + 智能体 GGUF 4.68；SD1.5 三件套（RealVis 3.97 + VAE 0.31 + LCM-LoRA 0.13 = 4.41 GB）留在 `models\` 当后备但**不进包**（引擎已不注册）。整包体积（`dist\ChenguAIStudio_Portable` 111.2 GB、估 120-130 GB）仍是 2026-11-08 的实测口径，换型后尚未重新打包复测——真要发布前重跑一次 `scripts\build_portable.ps1` 再回填数字。
pwsh -File scripts\build_portable.ps1                 # 完整打包 → dist\ChenguAIStudio_Portable\
pwsh -File scripts\build_portable.ps1 -SkipModels     # 调试：跳过权重复制
pwsh -File scripts\build_portable.ps1 -SkipVenvs      # 调试：跳过虚拟环境复制
pwsh -File scripts\build_portable.ps1 -OutDir E:\包   # 指定输出目录
```

包内容：应用代码 + **四个虚拟环境**（`.venv`/`.venv-image`/`.venv-video`/`.venv-music`）+ **包内自带 CPython 3.11 运行时**
（目标机无需装 Python）+ 已就位的权重（Sana-1.6B 多语言版 9.06GB、Wan2.1 44.18GB、ACE-Step 9.40GB、Qwen3 GGUF 4.68GB；未注册的 SD1.5 三件套 4.41GB 目前也还在盘上；缺哪块权重/venv 对应模块自动不注册）+
`启动辰鹄.bat` / `启动桌面端.bat` + **`打包与迁移说明.md`**（独立说明文档：迁移方法、显存门槛、各模型占用、FAQ）。
打包时写入 `build_manifest.json`（记录打包机原路径，供目标机首启替换）。

### 目标机首次启动（`启动辰鹄.bat`，全程本地）

1. **路径自适应** `scripts\fix_portable_paths.py`（幂等，支持反复搬家再次改写）：把各 venv `pyvenv.cfg` 的
   `home=` 指向包内 `runtime\python311`；把 `pyvenv.cfg`、site-packages 内 `*.pth` / `__editable__*_finder.py`
   里记录的旧绝对路径整串替换为新包根（ACE-Step 的 editable 安装由此继续生效）；首次生成包内
   `data\apphome\config.json`（`engines_enabled=true`，路径键留空走相对默认）。
2. **启动前置硬件检测** `scripts\check_hardware.py`（只读本机 nvidia-smi + Windows 内存状态，不联网）：

   | 评级 | 条件 | 效果 |
   |------|------|------|
   | **达标** | 内存 ≥60GB（64GB 档兼容阈值）且显存 ≥12288MB | 全部模块可用 |
   | **中等** | 显存 <12288MB（≥6144MB），或显存够但内存不到 64GB 档 | 自动禁用音乐与视频，仅保留图片 |
   | **低配** | 无 NVIDIA / 显存 <6144MB / 内存 <16GB | 提示性能不足，所有生成引擎不注册，仅可浏览历史与产物 |

   实现：检测结果经环境变量 `CHENGUAI_DISABLE_MODALITIES` 注入，`engine_registration.py` 对应引擎**不注册**
   （图片/视频/音乐模块自身代码零改动）；明细写项目内 `logs\hardware_report.json`（本地文件，不上传）。
   阈值在 `check_hardware.py` 顶部常量，可自行调整。
3. 注入 `CHENGUAI_DATA_ROOT=<包>\data`、`CHENGUAI_APP_HOME=<包>\data\apphome`、`HF_HUB_OFFLINE=1` 等离线锁，
   启动 Web 控制台（127.0.0.1）并自动开浏览器；`启动桌面端.bat` 则起 PySide6 桌面端。

### 路径适配（本段唯一的既有代码改动，均为适配性、不改功能逻辑）

- `app\core\paths.py`：默认数据根由写死的 `D:\ChenguAI` 改为**项目根下 `data\`**（原路径在本机从未存在，
  无迁移成本；现有开发机 config.json 已保存的旧值不受影响）；新增 `CHENGUAI_APP_HOME` 环境变量覆盖
  配置/数据库/日志位置（此前只认 `%LOCALAPPDATA%\ChenguAIStudio`）。
- `app\engine_registration.py`：新增 `_disabled_modalities()` 门控（默认不禁用，开发环境行为不变）。
- 其余全部为新增文件（打包/检测/修复脚本、bat 模板、独立说明文档）；**所有功能、Tab、参数记忆逻辑保持不变**。

### 离线保证（可核查）

- 无任何遥测/上报/云端推理代码路径；Web 仅监听 127.0.0.1；启动即注入 HF 离线锁。
- 权重完整性校验（SHA-256 / safetensors 文件头）全部在开发机打包前完成，目标机不做任何联网校验。

**执行状态**：**真实完整打包已实测完成**（`dist\ChenguAIStudio_Portable`，含全部代码/venv/当时的 FLUX+ACE-Step 权重，111.2 GB；FLUX 此后已删除、换成 Sana 9.06 GB，整包未重新复测）；此后每轮开发以 `robocopy /MIR` 对 dist 做增量同步；Wan2.1 权重（44.18 GB）与 `.venv-video` 已加入打包清单，dist 下次增量同步后整包约 **120-130 GB**（2026-11-08 重算：权重 62.97 GB + 四 venv/运行时，FLUX 33.73 GB 已剔除；确切体积待下次打包实测回填，D 盘剩余 170.25 GB 已核实充足）。

---

## 🤖 第六板块「AI 智能体」+ 🌏 界面语言（2026-11-06 开发完成，等待人工运行验收）

### 新增模块

| 文件 | 作用 |
|------|------|
| `app/agent/router.py` | 自然语言 → 任务意图（规则优先，中英都能跑）：判 t2i / i2i / t2v / i2v / music / chat / copy / speak / **document（要的是 .docx/.xlsx/.pptx 文件）**，并从话里抠 seed、steps、尺寸与比例、时长（秒 → `4n+1` 帧）、bpm、纯音乐、人声语种；垫图 + 「动起来」判成 i2v，垫图 + 「改成」判成 i2i 并剥掉指令词 |
| `app/agent/llm.py` | 只连本机 OpenAI 兼容端点（默认项目自带的 llama.cpp 服务，`llm_endpoint`/`llm_model`）；`resolve_model()` 在配置的模型名本机不存在时自动在同族里挑可用的（config 写 `qwen2.5:7b-instruct`、本机只有 `qwen2.5:14b-instruct` → 自动落到后者并在回复里说明，不静默假装）；加载权重期间的 503「Loading model」译成「正在加载权重，稍等几秒再发一次」而不是甩一个像故障的状态码；`unload()` 先试 llama.cpp 的 `POST /unload`（**实测 b12182 没有这个路由，回 404** → 交还显存实际由 `local_server.release_vram()` 停进程完成），端点是用户自己的 Ollama（没有 `/unload`）时回退到原生 `/api/generate` + `keep_alive=0`（12GB 单卡上大模型与图像/视频/音乐生成引擎抢同一块显存） |
| `app/agent/local_server.py` | 项目自带大模型服务的看护：`tools\llama_cpp\llama-server.exe` + `models\llm\gguf\*.gguf`，只绑 `127.0.0.1:11435`；**就绪判定必须问模型本身**——`port_open()`（端口在听）与 `model_ready()`（`GET /v1/models` 回 200）分开，`status()` 里 `running` / `ready` / `loading` 三个状态各说各的，因为 llama-server 是**先绑端口、后加载权重**，加载期间整个端点回 503；`ensure_running()` 在**聊天要用模型时**按需拉起（正在加载就接班陪等，绝不重复起第二个实例抢同一块显存），非回环端点与别人的端口（例如用户自己的 11434）一律拒绝代启动；`stop()` 只杀自己 pid 文件里、镜像名确实是 `llama-server.exe` 的那个进程；`release_vram()` 生成入队前把自家服务停掉交还显存（下次聊天自动重载，代价 30–90 秒） |
| `app/agent/speech.py` | Windows SAPI 朗读（免权重、可商用，Huihui/Kangkang/Yaoyao/Zira）；只承诺「朗读」，不假装能做听写 |
| `app/agent/documents.py` | 「做文件」的落盘层：把大模型写出的结构落成 **`.docx` / `.xlsx` / `.pptx`**（`python-docx` / `openpyxl` / `python-pptx`，纯本地写 OOXML，**运行期一行都不联网**）。`parse_outline()` 吃「# 大标题 + ## 小节 + `-` 要点」，`parse_table()` 吃 `|` 分隔的表格（散文自动退成「项目 / 内容」两列），`skeleton_plan()` 在大模型没就位时按题目搭骨架并把正文标成「（待补充）」；上限（40 节 / 400 行 / 30 列 / 60 页）与 Windows 非法文件名字符清理都在这一层；缺库时 `DocumentEngineMissing` 给出「装哪一个」的人话，不抛 ImportError 堆栈 |
| `app/agent/service.py` | 路由结果交给**同一个 `TaskService`**：生成走 FIFO 队列（与五个 Tab 完全同一条提交路径、同一套显存预检），聊天/文案走大模型，朗读走 SAPI；大模型不可用时 `degraded=True` + 诚实降级文案，绝不编造回复 |
| `app/web/server.py` | 新接口 `POST /api/agent/command`、`GET /api/agent/capabilities`、`POST /api/uploads`（后缀白名单 + 魔数校验 + 时间戳前缀）、`GET /uploads/<name>`；输出文件支持单段 Range（206/416）；Host/Origin 同源校验（403 拒跨站）；硬件快照 5s TTL |
| `app/web/static/agent.js` + `index.html` | 网页端第一个 Tab = AI 智能体（聊天气泡、能力 pill、示例 chip、垫图、任务跳转按钮、朗读按钮） |
| `app/ui/pages/agent_page.py` + `creation_page.py` + `main_window.py` | 桌面端第一个 Tab 同上（`QThreadPool` 后台调用，UI 不冻结；Enter 提交、Shift+Enter 换行；切走时 feed/composer/参数面板自动恢复） |
| `app/core/i18n.py` + `app/web/static/i18n/` | 桌面与网页**共用同一份词典**：`index.json`（18 项注册）+ `source-zh.txt`（141 条中文源串）+ 17 门语言 JSON |

### 🌏 语言菜单

- 18 个选项：简体中文 + en / ja / ko / es / fr / de / ru / pt / it / nl / pl / tr / ar / hi / vi / th / id；**同一时刻只显示一种**，切换即时换壳、不重启。
- 持久化：网页 `localStorage.chenguai.lang`；桌面写 `config.json` 的 `ui_language`（重启后保持）。
- 翻译范围 = **界面外壳**（Tab、侧栏、按钮、占位符、状态徽标、系统页标题与返回按钮）；引擎回报的长错误（显存不足、权重缺失、引擎未注册…）**保持中文**，避免出现「看着像译文、其实是没翻」的假象。
- 桌面端用 `is_source()` 白名单：只有登记过的源串才换壳，会话标题、GPU 摘要、任务正文不会被误改；`ar` 仅设 `body.rtl{text-align:right}`，三栏顺序不变（有意折衷）。

### 顺带修掉的运行缺陷（见「纠错」报告逐条）

- `variation()`（V 变体）改取**源任务自己的 seed**，不再读面板当前模式的参数桶（面板停在视频/音乐 Tab 时会用错种子或退化成随机）。
- 显存探测改为守护线程缓存（`cached_gpu_info` / `prime_gpu_cache` / `nvidia_smi_available`）：UI 线程不再同步跑 `nvidia-smi`（卡被占满时一次 1s+）；探测结果未知时不再误报「检测不到显卡」，放行交给引擎按真实显存判定。
- 首页「暂无可用本地视频引擎 · 调研中」→ 按引擎注册状态实时显示 Wan2.1（含 `_engine_names()` 里的视频引擎）。
- 错误文案里 `scripts\\setup_video_venv.ps1` 多写一层反斜杠已修；QSS 选择器 `QFrame#userBubble` → `QLabel#userBubble`（提示词气泡此前根本没套上卡片样式）；大图查看器加 `WA_DeleteOnClose`（否则每次打开都留一个实例 + 全尺寸 QPixmap 常驻内存）。

### 回归测试

`.venv\Scripts\python.exe -m pytest -q` → **51 passed, 1 skipped**（2026-11-07 专项复核后为 **61 passed, 1 skipped**，见下一节）。本轮新增：`tests/test_agent_and_i18n.py`（12 项：路由判定/帧数收口/参数抠取 + 词典逐字节一致性，禁止 value 与中文 key 相同）、`tests/test_desktop_ui.py`（4 项离屏 UI：六 Tab 顺序、语言切换与持久化、智能体页接管/恢复、Enter 提交）。

### 等待人工验收（在此之前不得当作成品）

本轮**没有**自动跑任何 GPU 生成。请先 `scripts\release_local_engine.bat` 释放显存，再依次验：文生图 / 图生图 / 文生视频 / 图生视频 / AI 音乐（中文提示词）与智能体一句话生成。**Web 控制台必须重启**才加载新代码（8765 上的旧进程仍在跑改动前的版本）。

---

## 🩹 「AI 智能体」专项修复 + 网页端可用性（2026-11-07 开发完成，等待人工运行验收）

本轮检索范围**只限「AI 智能体」板块**（含它依赖的网页端通道）。桌面端与网页端**两端都保留、都必须能起**，两端共用同一条 `TaskService` FIFO 队列。

### 逐条问题与处置

| # | 症状 | 根因 | 处置 |
|---|------|------|------|
| 1 | **网页端「没了」**：页面打不开 / 所有接口 403 | `server.py` 的 `_check_local_only()` 把 Host 与 Origin 按**字符串严格相等**比较。本机代理把 Host 改写成 `127.0.0.1:8765`、浏览器发的 Origin 是 `http://localhost:8765` → 每个请求判死；日志里连 Host 都不记，完全无从排查 | 回环地址互认为同源（`localhost` / `127.0.0.1` / `::1` / `127.x.x.x` / 绑定地址本身），`[::1]:8765` 这类带端口写法一并处理；wildcard 绑定（`0.0.0.0`）仍要求同源；403 正文写明「请用 http://127.0.0.1:8765/ 打开、代理请加白名单」，并 WARNING 记录真实 Host/Origin。**只放宽到本机回环，没有开放局域网** |
| 2 | 端口冲突时网页端起不来，只甩 traceback | `OSError 10048/10049` 未处理 | `main()` 捕获：已有实例在跑 → 中文提示（`-Restart` 或改 `web_port`）并返回退出码 3，其他 `OSError` 返回 1 |
| 3 | 网页端没有可靠启动入口（起没起、谁占端口都不知道） | — | 新增 `scripts\run_web.ps1` + 可双击的 `scripts\run_web.bat`：`-Restart` / `-Status` / `-Foreground` / `-Port` / `-NoBrowser`；端口从 `config.json` 读；**只认本项目自己的进程**（命令行含 `app.web.server` 或可执行文件是本 venv），别人的端口不代杀；起完自动开浏览器，失败打印日志尾部。venv 的 `python.exe` 会派生子进程持有监听套接字，故按属主逐个判 |
| 4 | 智能体排队的生成**不在左栏会话流里**，刷新就找不到 | 会话分组是网页端前端 localStorage（`TaskRequest` 无 session_id），agent.js 提交后没登记 | `app.js` 新增 `attachAgentTask()`（导出 `window.ChenGuAttachTask`）：批内补进原网格、否则新建卡片，`persist()` + 重绘并滚到底；agent.js 在任务回执后调用它（kind/mode 按 modality 回退）。桌面端无此缺陷（`taskQueued` → `poll()` 早已接通） |
| 5 | 智能体气泡复制后没有视觉反馈 | class 写成 `agent-dim`，CSS 里只有 `.agent-bubble.dim` | `agent.js` 两处改 `dim` |
| 6 | 智能体聊天后，紧接着的生成常被「空闲显存不足」拦下 | 12GB 单卡上 Ollama 常驻 6–9GB 与图像/视频/音乐生成引擎抢同一块显存；且 `llm` 客户端超时 90s 不够 9B 冷启动 | 新增配置键 `llm_timeout_seconds`（默认 240，范围 15–900）；`AgentService._generate()` 在**入队之前**调 `llm.unload()`（Ollama 原生 `/api/generate` + `keep_alive=0`），回执追加「已让本机大模型交还显存给生成引擎。」，返回 `freed_vram` |
| 7 | 默认模型 `qwen2.5:7b-instruct` 本机根本不存在，每次都得回退并多一句说明 | 默认值与实际模型库不符 | 默认值与本机 config 一并改为 **`qwen3.5:9b`**（Ollama 已装，6.6GB，与 `resolve_model()` 的同族首选顺序一致） |
| 8 | 便携包里没有大模型，换机后智能体只剩模板回复 | 权重只散在 `D:\OllamaModels` | 新增 `scripts\import_llm_model.ps1`：按 manifest 的 config+layers digest **精确复制** blob 与 metadata（跳过 `-partial` 半成品）到项目内 `models\llm\ollama_models\`；已导入 **qwen3.5:9b / 6.1GB**（8 个 digest，含 5.24GB 主权重 + 0.86GB vision projector），报告写 `models\llm\ollama_models\imported-*.json`；`-List` 看源库、`-Restore` 用 robocopy 放回 Ollama store。换机后设 `OLLAMA_MODELS` 指向该目录即可离线跑 |

本机 `%LOCALAPPDATA%\ChenguAIStudio\config.json` 已写入：`llm_model = qwen3:8b`、`llm_timeout_seconds = 240`、`llm_endpoint = http://127.0.0.1:11435/v1`（智能体专用实例，见下）。

### 🧠 给「AI 智能体」专配一个大模型（qwen3:8b，推理程序与权重都在项目内，**不依赖 Ollama**）

智能体的聊天/文案不能靠运气：机器上装没装推理框架、装的是哪个模型、重启之后还在不在跑，都会让它只剩模板回复。所以这一轮给它配一套**自带**的：

| 部件 | 位置 / 命令 | 说明 |
|------|-------------|------|
| 推理程序 | `tools\llama_cpp\llama-server.exe`（同目录带 CUDA 12.4 运行库 DLL） | llama.cpp 官方 Windows 包（b12182），解压即用，暴露 OpenAI 兼容 `/v1/chat/completions` |
| 权重 | `models\llm\gguf\Qwen3-8B-Q4_K_M.gguf`（4.68 GB，Apache-2.0） | GGUF 单文件，拷走就能用；换机重跑 `-Setup` 会重新下（走 hf-mirror，国内直连可达） |
| 一次装齐 | `scripts\agent_llm.ps1 -Setup` | 按有无 `nvidia-smi` 自动选 CUDA 包或 CPU 包（GitHub 走 `gh-proxy.com` / `ghproxy.net` 镜像，`curl -C -` 断点续传）→ 下 GGUF（hf-mirror → huggingface 依次试）→ 把 `llm_endpoint` / `llm_model` / `llm_model_path` 写进 config.json → 起服务 |
| 起 / 停 / 状态 | 双击 `scripts\run_agent_llm.bat`，或 `scripts\agent_llm.ps1 -Start` / `-Stop` / `-Status` | 只绑 **127.0.0.1:11435**，不联网、不开放局域网；`--alias qwen3:8b` 让 config 里的模型名继续有效；`--fit on` 按当时空闲显存自动决定 offload 层数；`-Stop` 只停 pid 文件里那个 `llama-server.exe`，端口被项目外程序占着就报错、不代杀。**你自己 11434 上的 Ollama 与它的模型库完全不碰** |
| 按需自动拉起 | `config.json` 的 `llm_autostart`（默认 `true`） | 聊天要用模型而端点没在听 → `app\agent\local_server.py` 把自带服务直接拉起来（首次加载权重 30–90 秒，界面靠「重新探测」自己变绿；已有实例在加载就接班、不重复起第二个抢显存）。**重启电脑后不必再手动起**，这正是换掉 Ollama 的主要动因 |

为什么选 **Qwen3-8B 的 Q4_K_M**：12GB 单卡上 Q4_K_M 约吃 5GB，中文指令跟随与文案质量够好；`--fit on` 让 llama.cpp 按**当时真正空闲的显存**决定进 GPU 的层数，显存被别的东西占着也能跑（只是慢一些），不会起手就 OOM。要更省显存换 `Qwen3-4B` 的 Q4_K_M，要更强就换更大的 GGUF——把文件丢进 `models\llm\gguf\` 即可（多个时取最大的），或在 config.json 里用 `llm_model_path` 写死。

`llm_endpoint` 默认 `http://127.0.0.1:11435/v1`、`llm_model` 默认 `qwen3:8b`（就是 llama-server 的 `--alias`），与脚本默认值一致；连不上端点时的报错会直接给出该跑的那条命令（`scripts\run_agent_llm.bat`，第一次用还有 `-Setup`），同时也接受用户自己的端点（例如自家 Ollama：`http://127.0.0.1:11434/v1`），不再只说「检查网络」。

**有意没做**：会话分组仍是前端 localStorage 的事（`TaskRequest` 里没有 session_id），本轮按范围只在前端补齐挂接，不动任务模型；`scripts\release_local_engine.bat` 仍**不释放自带大模型**（要手动腾显存：`scripts\agent_llm.ps1 -Stop`；智能体在生成入队前会**停掉自家服务进程**把整块显存交还给生成引擎，见最后一节）；`scripts\import_llm_model.ps1` 与旧的 Ollama 模型库 `models\llm\ollama_models\` 属**旧链路：脚本保留、不再使用**，那 11GB 模型库副本按用户决定删除，需要时重跑该脚本即可再导回来。

### 复核结果（自动化部分）

- `.venv\Scripts\python.exe -m pytest tests -q` → **64 passed, 1 skipped**。`tests/test_agent_and_i18n.py` 增至 25 项，新增：`unload()` 三态（调用参数/无已知模型空转/失败不炸）、超时余量、`llm_timeout_seconds` 配置校验、「交还显存」必须排在入队**之前**、Host/Origin 回环放行 + 外站拒绝 + wildcard 同源、一项**真跑 OpenAI 兼容协议**的聊天（临时只监听 127.0.0.1 随机端点的假端点，钉住 `/models` 选型与 `/chat/completions` 报文，不占显存、不联网，杜绝「悄悄退回模板」）、`resolve_model()` 必须把选中的模型名记进 `_last_model`（否则 `unload()` 不知道该卸载谁）、连不上端点时的报错必须点名 `scripts\run_agent_llm.bat`、以及一条**专用大模型实例的安全护栏**（读 `scripts\agent_llm.ps1` / `run_agent_llm.bat` 文本，钉住「只绑 127.0.0.1」「模型库在项目内 `models\llm\ollama_models`」「端口 11435 与标准 Ollama 分开」，并禁止出现 `0.0.0.0` 之类的非回环绑定）。
- 下载踩到的坑（已修，写在这里免得下次再踩）：`ollama pull` 把 5.2GB 切成 16 个 326MB 分片并行下，本机这次最后两片反复断连、`-partial-*` 一直回零。脚本改成**最多 4 轮自动重试**（分片进度不丢），并且验证必须看 `/api/tags` 里有没有这个名字。这里还有个 Windows PowerShell 5.1 的硬坑：`Where-Object` 只命中一个对象时返回的是标量，标量没有 `.Count`，`$found.Count` 得到 `$null`，导致明明下好了却判成「没下到」、白重试四轮 —— 现在写成 `@(… | Where-Object …)` 并在注释里写明原因。
- 桌面端离屏冒烟 `scripts\smoke_desktop_generation.py` → **SMOKE PASS**（未启动任何 GPU worker）。注意这个冒烟脚本的 Tab 断言在加「AI 智能体」Tab 之后就一直是过期状态（会误报 SMOKE FAIL），本轮已改为断言**六个 Tab**（`agent` + 五创作模式）并对智能体 Tab 单独断言「智能体面板真的显示出来」——它故意不接管创作参数面板、也不把 `agent` 写进模式设置。另外这个脚本靠 cwd/`PYTHONPATH` 找到 `app` 包，直接 `python scripts\smoke_desktop_generation.py` 会 `ModuleNotFoundError: app`；在项目根跑并先 `$env:PYTHONPATH = (Get-Location).Path`。
- 活服务实测（8765，新代码已生效）：`Host=127.0.0.1:8765 + Origin=http://localhost:8765` 由 403 → **200**；`Host=evil.example.com`、外站 Origin 仍 **403** 且正文说明原因。
- `GET /api/agent/capabilities`（专用实例已起、config 已改）→ `llm.configured_endpoint = http://127.0.0.1:11435/v1`、`configured_model = qwen3:8b`、`available = true`、`model = qwen3:8b`、`models = [qwen3:8b, qwen3.5:9b]`；四引擎齐（`flux-schnell` / `wan-t2v` / `wan-i2v` / `ace-step-15`）；`tts.available = true`（zh-CN 四声 + en-US）。
- headless Edge 渲染 `http://127.0.0.1:8765/` → DOM 19.5KB、`mode-tabs` / `data-mode="agent"` / `agent-panel` 在位、控制台无 JS 报错；`app.js` / `agent.js` / `i18n.js` 三个文件 `node --check` 全通过。

### 等待人工验收（本轮同样没跑任何 GPU 生成）

> 下面这份步骤是按**旧 Ollama 链路**写的，历史保留；模型链路已在同日换成项目内 llama.cpp（见上节与最后一节），**按最后一节的验收步骤做**。

1. 智能体大模型：双击 `scripts\run_agent_llm.bat`（模型已在 `models\llm\ollama_models`，它只把专用实例起起来并确认 `qwen3:8b` 在位）。想确认状态：`scripts\agent_llm.ps1 -Status`。
2. 网页端：双击 `scripts\run_web.bat`（或 `scripts\run_web.ps1 -Restart`）→ 浏览器 <http://127.0.0.1:8765/> → 第一个 Tab「AI 智能体」。
3. 依次试四句：`你好，你能做什么` → `写一段 30 字的咖啡广告文案` → `画一杯冰美式，1024 方图` → `把这句话读给我听`。**第一句要等 qwen3:8b 冷启动**（最长 240s，之后 Ollama 五分钟内再问就快了）；生成那句应看到「已让本机大模型交还显存给生成引擎。」，并且 4 张图会同时出现在左栏当前会话里（缺陷 4 的验收点）。
4. 桌面端：`scripts\run_app.ps1`（**要重启**，桌面端启动时才读 config，改过的 `llm_endpoint` 才会生效）→ 第一个 Tab「AI 智能体」同样四句；两端可同时开着，任务按 FIFO 串行。
5. 显存：12GB 单卡上 qwen3:8b 常驻约 5GB，跑视频/音乐前先确认空闲显存够（`nvidia-smi`），或按上面的方式让 Ollama 卸载 / 停掉专用实例。

## 🩹 「无引擎 / 还是没有大模型」二次修复：大模型彻底搬进项目内，**不用 Ollama**（2026-11-07 下午 · 开发完成，等待人工真机验收）

屏上原话是 **「无引擎」**，追问确认现象是「AI 智能体还是没有大模型」；换链路的直接指令是**「不用 Ollama，直接下载一个！」**。两层原因叠在一起，任何一层都足够让能力条一直显示「无引擎 / 本机大模型：不可用」。

### 逐条问题与处置

| # | 症状 | 根因（实测） | 处置 |
|---|------|------|------|
| 1 | 能力条钉在「无引擎」不动，模型明明在却以为没有 | 桌面端与网页端的能力探测只在面板出现时跑**一次**并永久缓存；程序刚起来的几秒引擎与模型都还没就位，用户看到的一直是开机那一刻的快照 | 两端都加常驻**「重新探测」**按钮（进行中显示「正在重新探测…」），并在探测结果降级时自动重试 **8 次 × 8 秒**；词典新增这两条源串，18 语言全部翻译 |
| 2 | 专用大模型实例重启电脑后就不在了（`11435 → 无监听`） | 旧链路靠**另开一个 Ollama 实例**监听 11435，它不会自启 | 整条链路换成**项目自带**的 llama.cpp：`tools\llama_cpp\llama-server.exe`（带 CUDA 12.4 运行库）+ `models\llm\gguf\Qwen3-8B-Q4_K_M.gguf`（**4.68 GB 已下载并校验完成**），OpenAI 兼容 `/v1`，只绑 `127.0.0.1:11435`。聊天要用模型而端点没在听 → `app\agent\local_server.py` 的 `ensure_running()` **按需自动拉起**（`config.json` 的 `llm_autostart`，默认开）。**这就是换掉 Ollama 的主要动因** |
| 3 | 服务其实在跑仍被判「不可用」；加载途中被判死，用户以为装坏了 | llama-server **先绑端口、后加载权重**：加载期间 `/v1/models` 回 **503 `Loading model`**（本机实测加载约 **53 秒**），这 53 秒里「端口连得上」毫无意义；把 503 原样端出去就像硬件故障 | `LocalLlamaServer` 把 **`port_open()`（端口在听）** 与 **`model_ready()`（`/v1/models` 回 200）** 分开，`status()` 给 `running` / `ready` / `loading` 三个字段；`ensure_running()` 在「端口开着但没就绪」时**陪它等**（探测路径不傻等，返回 `loading` 让界面自己重试），并且**绝不因为「还没能用」就起第二个实例去抢同一块显存**；`app\agent\llm.py` 把 503「Loading model」译成「自带大模型正在加载权重（一般 30-90 秒），稍等几秒再发一次」 |
| 4 | 生成前排队的「交还显存」在新链路上会**空转** | 原方案靠 `POST /unload`（llama.cpp）或 Ollama `keep_alive=0`；**实测 b12182 没有 `/unload`、也没有 `/load`，两个都回 404** → 光打接口等于什么都没做 | 新增 `LocalLlamaServer.release_vram()`：**停掉我们自己起的那个进程**，整块显存当场归还；下次聊天 `ensure_running()` 自动重载（代价 30–90 秒，跟之前 Ollama `keep_alive=0` 是同一笔交易）。`AgentService._free_llm_vram()` 按端点归属分流：自家端口 → 停进程；用户自己的端点（例如 11434 上的 Ollama）→ 仍走它原生卸载。`stop()` / `release_vram()` **只认 pid 文件里且镜像名确实是 `llama-server.exe` 的进程**，端口上挂着别人的服务（含用户自己的 11434）一律不动 |
| 5 | 旧 Ollama 模型库 11 GB 留在项目里没人用 | 换链路后 `models\llm\ollama_models\` 失效 | 按用户决定**删除**（顺序不能反：等新 GGUF 到位并跑通之后才删）；D 盘剩余 **133.5 GB → 143.4 GB**。`scripts\import_llm_model.ps1` 保留但已标注【旧链路 · 不用】，需要时可再导回 |

配置默认值：`llm_endpoint = http://127.0.0.1:11435/v1`；新增 `llm_autostart`（默认 `true`）与 `llm_model_path`（默认空＝取 `models\llm\gguf\` 里最大的那个 GGUF）；`llm_model` **仍是 `qwen3:8b`**——靠 llama-server 的 `--alias qwen3:8b` 对上，界面 pill 与用户已有 config 都不用改。

### 复核结果（自动化 + 真机非生成部分）

- `pytest tests -q` → **75 passed, 1 skipped**（`tests/test_agent_and_i18n.py` 35 项）。本轮新增 5 条护栏：**端口在听 ≠ 模型可用**（`running`/`ready`/`loading` 三态、503 加载期间不得判可用、**不得起第二个实例抢显存**）、**只杀自己起的进程**（pid 文件里镜像名不符 → `foreign`，一条 `taskkill` 都不发）、**交还显存分两家**（自家 → 停进程；用户自己的端点 → 不代停；本来没在跑就不吹「已经腾出来了」）、503「Loading model」的文案里不得出现 `503` 这种像故障的字样、服务端支持 `/unload` 时优先用它且只打一次。
- 词典自检：**源串 143 条 · 语言 18**，每个 JSON 的 key 列表与顺序严格等于 `source-zh.txt` 且 value ≠ key（护栏测试绿）。`node --check app\web\static\agent.js` → OK。
- 真机（**没跑任何 GPU 生成、也没替用户发真实 LLM chat**）：`scripts\agent_llm.ps1 -Start` → 写 config → 加载权重 **53 秒** → `/v1/models` 返回 `id=qwen3:8b`（`--alias` 生效，`n_ctx` 8192，Q4_K_M）；`LocalLlamaServer.status()` → `running=true, ready=true, loading=false, owned_endpoint=true, managed_pid=35904`；`llm.status()` → `available=true, model=qwen3:8b, models=[qwen3:8b]`；`stop()` → 「已停止自带大模型服务（PID 33252）」、pid 文件已清、`11435` 无监听，**用户自己 11434 上的 Ollama（PID 18536）全程未动**。按需自动拉起也实跑验证过：第二次能力探测前进程已由代码拉起（PID 35904），回执写「已自动拉起自带大模型服务…首次加载权重要 30-90 秒」。
- 显存实况（要说实话的那条）：`--fit on` 下服务起来后 `nvidia-smi` used ≈ **11666 / 12288 MiB**，不是「模型只吃 5 GB」。所以**跑视频/音乐之前**要么先让智能体排一个生成任务（它会停掉自家服务），要么手动 `scripts\agent_llm.ps1 -Stop`；空闲不够时 `--fit on` 会把部分层留在内存里，能跑但明显变慢——这一句会写进界面回执，不假装没事。
- 桌面端离屏冒烟 `scripts\smoke_desktop_generation.py` → **SMOKE PASS**（未启动任何 GPU worker）。
- 已知残留（本轮未处理，只绑回环所以风险有限）：llama-server 启动日志提示 `no API key is set and CORS allows all origins`。要加固就在 `server_args()` 加 `--api-key` 并给 config 增加一个键，等你点头再做。

### 等待人工真机验收

1. **两端都必须重启**才加载新代码：网页端 `scripts\run_web.ps1 -Restart`（8765 上现在跑的还是改动前的进程），桌面端 `scripts\run_app.ps1`（config 只在启动时读）。浏览器还要 Ctrl+F5。
2. 可先只看状态：`scripts\agent_llm.ps1 -Status`；或者什么都不起——第一句聊天会自己把它拉起来。
3. 智能体第一句 `你好，你能做什么`：**首次冷启动要等 30–90 秒**，期间界面显示「正在重新探测…」，加载完自己变绿，也可随时点「重新探测」。随后 `写一段 30 字的咖啡广告文案` 要出真文案（不是模板回复）。
4. 接着 `画一杯冰美式，1024 方图`：回执应出现「已停掉自带大模型服务，整块显存让给生成引擎；下次聊天会自动重载权重（30-90 秒）。」——这句就是本轮「交还显存」的验收点（旧链路靠 `keep_alive=0`，新链路只能停进程）。
5. 再问一句聊天：确认它**自动重新加载**并回复（这次要再等 30–90 秒），能力条也会跟着变绿。
6. 桌面端与网页端**同时存在**、可同时开着，任务按 FIFO 串行；两端任一缺失都算不合格。

## 🩹 三条硬要求一次改齐：网页端「无引擎」真相 / 文生图·图生图拆成两个引擎 / 产物保存位置（2026-11-07 傍晚 · 开发完成，等待人工真机验收）（历史：本节里的 `flux-schnell` / `flux-i2i` 两个引擎 id 已于 2026-11-08 换成 `sd-realvis` / `sd-realvis-i2i`；「拆成两个引擎 + 显存互斥卸载」「产物落 output\」这两条机制仍然有效）

这一轮是被三条批评逼出来的：**「网页版依然没有引擎」「桌面版就没动过」「文生图和图生图共用一个模型太吃显存」「生成好的内容要能存到项目内 output 或本地硬盘」**。这次改成什么样，全部用「自己渲染 + 自己实例化」取证，不再靠猜。

### 1️⃣ 网页端 AI 智能体为什么一直显示「无引擎」——真相与修复

- **根因**（headless Edge + CDP 点进 AI 智能体 tab 抓 DOM 实锤）：`app/web/static/app.js` 的 `refreshEngines()` 用 `MODALITY[state.mode]` 过滤引擎，而 `MODALITY` 里**没有 `agent` 这个键** → 过滤结果为空 → 直接走「无引擎」分支。引擎其实 5 个都注册着（只是没启动），界面却把它说成「没有」。**不是没装引擎，是前端把「按板块自动选引擎」这个模式误报成「没有引擎」。**
- **修复**：`refreshEngines()` 在无 modality 映射（＝AI 智能体 tab）时改走新增的 `renderAgentEngineOverview()`：顶栏胶囊显示中性灰 `5 个引擎 · 未启动`，引擎下拉显示「按板块自动选择（文生图/图生图/视频/音乐）」并置灰（这个模式下本来就不该手选），右侧说明写清「已注册未启动 ≠ 没有引擎，点「启动」或由智能体自动拉起」。
- 顺带把「未启动 / 未注册」两种状态分开：引擎**真的没注册**才标红 `无引擎`；只是没启动 → 中性灰 `xxx · 未启动`（两端同规则）。

### 2️⃣ 两端必须一样：桌面端与网页端能力条逐字对齐（含此前漏掉的「图生图」槽）

`app/ui/pages/agent_page.py`（桌面）与 `app/web/static/agent.js`（网页）现在都列**五个板块槽**并按 `engine_states` 分三态。两边实测文字（一边是浏览器渲染，一边是 offscreen 实例化真实装配链）：

```
文生图：flux-schnell · 未启动      图生图：flux-i2i · 未启动      文生视频：wan-t2v · 未启动
图生视频：wan-i2v · 未启动         音乐：ace-step-15 · 未启动
本机大模型：qwen3:8b（绿）         系统语音：5 个声音（绿）
提示：文生图与图生图是两个独立引擎，各自独立进程、互斥加载：同一时刻只会有一个把模型搬进显存，不会双份占用
```

防回归：`tests/test_agent_and_i18n.py::test_agent_capability_lists_every_slot_on_both_ends`（同时校验 HTML 元素 id 与 app.js 用到的 id 一致，防「面板空白」那类事故）。

### 3️⃣ 文生图 / 图生图拆成两个引擎（显存互斥加载）

| | 文生图 | 图生图 |
| --- | --- | --- |
| 引擎 id | `flux-schnell` | **`flux-i2i`（新增）** |
| 进程 | 独立 worker 进程 | 独立 worker 进程（另一个） |
| 权重 | `models\flux-schnell` | 同一份权重（不额外占盘），但**显存里只会有一个** |
| 角色校验 | 收到垫图 → 直接报错，不悄悄改用别的模型 | 缺垫图 → 直接报错 |

- 注册：`app/engine_registration.py` 用 `CHENGUAI_FLUX_IMAGE_ROLE=t2i|i2i` 把同一份 adapter 注册成两个引擎；`app/tasks/flux_worker.py::validate_request()` 做角色硬校验。
- 互斥：任务开跑前 `engine_manager.release_idle_engines(本次引擎)`（`app/tasks/service.py`）会把其它空闲引擎释放掉，所以 `flux-schnell` 与 `flux-i2i` **永不同时驻留显存**（12GB 卡上 llama-server 一起就要 11666/12288 MiB，必须互斥）。
- 界面：图生图板块的引擎下拉只列 `flux-i2i`，文生图板块只列 `flux-schnell`（`MODE_ENGINE_ALLOW`，桌面 `param_panel.py` / 网页 `app.js` 同一份语义）；切板块不会把上一单的引擎串过来（`creation_page._engine_for`）。
- 智能体侧：`_ENGINE_CANDIDATES["i2i"] = ("flux-i2i",)`，说「把这张图改成水彩风」命中图生图引擎；能力条与回执都会点名是哪个引擎。

### 4️⃣ 产物保存：工具内即时显示 + 项目内 `output\` 固定一份 + 自选目录 + 当场「另存」

1. **写进「输出目录」**（`config.output_dir`）：工具里即时预览、任务记录按它管理，链接不会失效。
2. **固定镜像到项目内 `output\`**：新增 `AppPaths.project_output_dir`（＝项目根 `output\`），`app/bootstrap.py`（桌面）与 `app/web/server.py::build_services()`（网页）都调用 `task_service.set_project_output_dir(...)`；`app/core/artifacts.py::copy_artifacts()` 复制，重名自动加 `-1/-2` **绝不覆盖**；输出目录本来就指着 `output\` 时**不重复复制**（`_same_dir` 判定）。
   > 历史产物在 `D:\ChenguAI\Output`（旧便携包的 data root），所以这轮**没有**动 `output_dir`，改用镜像——旧任务记录的预览链接保持有效。想把默认位置挪走，自己在设置里改「输出目录」即可。
3. **可选自动另存目录** `auto_save_dir`（绝对路径，可留空；可填移动硬盘/网盘目录）：网页端右栏「产物保存」面板（`/api/settings` GET/POST，相对路径会被 400 挡掉并即时建目录）＋桌面端「系统设置 → 输出与路径」，**同一份 config.json，任一端改完另一端立刻生效**（`_apply_runtime_settings` / `_update_settings` 都即时推给 TaskService）。结果卡上会显示「已自动另存 N 份 → 目录」。
4. **当场挑位置（手动另存）**：网页端每张结果卡 `⬇ 另存(到硬盘)` + 大图查看器（`showSaveFilePicker` 优先，浏览器不支持就退回 `?download=1` 下载）；桌面端图片/视频瓦片 `⬇ 另存`、音乐播放条 `⬇`、大图查看器「另存为…」（`QFileDialog`，可选任意盘符/移动硬盘/网盘）。复制不移动，项目内那份始终保留。

### 复核结果（自动化 + 真机非生成部分；本轮同样没跑任何 GPU 生成）

- `pytest tests -q` → **83 passed, 1 skipped**（本轮新增：`tests/test_artifact_saving.py` 全套＝重名不覆盖 / 缺文件只跳过 / 同目录不重复复制 / 完成的任务确实镜像到两处 / 设置校验与即时生效；`test_agent_and_i18n.py` 新增两端能力条一致性 + 引擎槽分离）；`node --check app.js / agent.js` 通过。
- 新代码的网页控制台（重启后的进程）实测：`/api/settings` 返回 `project_output_dir` 指向项目根 `output\`；`/api/agent/capabilities` 的 `engines` 含 `i2i: flux-i2i`、`engine_states` 五个 `stopped`、notes 含互斥加载说明。
- 浏览器渲染取证（headless Edge，CDP 点「AI 智能体」tab）：顶栏胶囊 `5 个引擎 · 未启动`（中性灰 `rgb(167,171,179)`，不再是红色「无引擎」），能力条五个槽全部中性 + 大模型/语音两个绿色，说明行含互斥加载。
- 桌面端取证（`QT_QPA_PLATFORM=offscreen`，真实 `ConfigStore` + `register_production_engines` + `AgentService` + `AgentPage`）：QLabel 实测与网页端逐字一致（`pill` 中性 / `pillOk` 绿）。

### 等待人工真机验收（这一步只能你在真机上看）

1. **两端都要重启**才吃新代码：网页端 `scripts\run_web.ps1 -Restart`（我已用它重启过，浏览器仍需 Ctrl+F5，静态资源版本号已升到 `?v=20261107c`）；桌面端关掉旧窗口再 `scripts\run_app.ps1`（新「⬇ 另存」按钮与新设置行都在新进程里）。
2. 出图前先腾显存：`scripts\release_local_engine.bat`（或网页端右栏那个提示）。
3. 打开 AI 智能体：两端都应看到 **`图生图：flux-i2i`**；顶栏不再是「无引擎」而是 `5 个引擎 · 未启动`。
4. 图生图板块放好垫图生成 → 回执/引擎区应点名 `flux-i2i`；文生图板块里**不该**出现 `flux-i2i`；把垫图硬塞进文生图会明确报错，不会悄悄换模型。
5. 生成完成后核对保存：工具里能看 → 项目根 `output\` 出现同名文件 → 若设了自动另存目录，那里也有一份且结果卡显示「已自动另存 N 份 → …」→ 点 `⬇ 另存` 能弹保存框、能选到别的盘/移动硬盘，重名会加 `-1`。
6. 两端必须同时在（任一缺失算不合格），任务仍是一条 FIFO 串行队列。

---

## 🧠 AI 智能体两件事：大模型加载参数升级 + 会做 Word/Excel/PPT 文件（2026-11-07 夜 · 开发完成，等待人工真机验收）

本轮只做「AI 智能体」板块内的两件事，**按顺序**先任务一后任务二；五个生成模块（文生图 / 图生图 / 文生视频 / 图生视频 / AI 音乐）的代码一行没碰，任务二期间也不再改大模型的加载参数。

### 1️⃣ 任务一：自带大模型的三个加载参数（已生效，llama-server 已重启）

`app/agent/local_server.py` 里三个常量 + 一次重启，实测命令行：

```
"…\tools\llama_cpp\llama-server.exe" --model "…\models\llm\gguf\Qwen3-8B-Q4_K_M.gguf"
  --alias "qwen3:8b" --host 127.0.0.1 --port 11435
  --ctx-size 16384 --cache-type-k q8_0 --cache-type-v q8_0
  --fit on --fit-ctx 16384 --flash-attn on --jinja --fit-target 3686
```

| 要求 | 改法 | 真机取证 |
|------|------|----------|
| 上下文 8192 → **16384** | `LLM_CTX_SIZE = 16384` → `--ctx-size 16384`（并把 `-fitc/--fit-ctx` 一起抬到 16384，否则 fit 只按 4096 估） | `GET /slots` 四个槽全部 `n_ctx=16384`（`/props` 里没有 `n_ctx`，只有 `/slots` 有） |
| KV cache 量化，**K 与 V 都要** | `LLM_KV_CACHE_TYPE = "q8_0"` → `--cache-type-k q8_0 --cache-type-v q8_0`（llama.cpp b12182 里 V 默认不量化，必须单独给 `--cache-type-v`） | 命令行里两个参数都在 |
| **显存至少留 30% 空闲** | `free_vram_target_mib()` 用 `nvidia-smi` 读整卡总量 × 30%（读不到退回 3686 MiB），喂给 `--fit on --fit-target <该值>`；这是 b12182 里唯一能控制「给这张卡留多少白」的杠杆（`--fit` 只调**我们没显式设置**的参数，所以 `--ctx-size` 仍在） | `--fit-target 3686` 在命令行里；12288 × 30% = 3686 MiB |

能力面板（两端同一个出口）现在会把这行念出来：「自带大模型加载参数：上下文 16384、KV 量化 q8_0；显卡整卡 … MiB，已用 …，空闲 …（要求至少留 3686 MiB）」。**显存数字只在能力面板里现探**（`status(probe_vram=True)` 才开一次 `nvidia-smi`，10 秒缓存），别的路径不碰 `nvidia-smi`，免得拖慢别的调用方。

### ⚠️ 但「空闲 ≥3686 MiB」这台机器当前达不到，原因不在模型参数

实测（`nvidia-smi` + `Get-Counter "\GPU Process Memory(*)\Local Usage"`，整卡 12288 MiB）：

- 停掉我们自己的 llama-server 后，基线 **used 10799 / free 1315**——也就是说卡上本来就只剩 1.3 GB 白。
- 起服务后 **used 11825 / free 289**。
- 占显存的是**第三方常驻程序 `strata.exe`（PID 21024，`C:\Users\cdchk\AppData\Local\Strata\…\engine\0.1.56-windows-x86_64-cuda-cuda13\strata.exe`）独占 10018 MiB**，另有 `dwm` 98 MiB。

所以 12288 −（第三方 10018 + dwm + 系统） < 3686：**除非先把第三方那个占 10 GB 的程序关掉，否则任何模型参数都不可能让空闲达到 30%**。我们没有动、也不会去杀那个进程。`--fit-target 3686` 的作用是让 llama.cpp 自己去凑这个白：它会把 offload 压到接近 0，也就是**当前基本走 CPU 推理，会慢**——这是「留 30%」这条要求在坏前提下的必然结果，参数按你的要求配好了，等卡空下来就自动变成 GPU 推理。

> 估算（**未实测**，别当事实）：卡真正空出来时全 offload ≈ 权重 4.68 GB + KV(q8_0, 16K) ≈ 1.2 GB ⇒ used 约 6–7 GB、free > 5 GB。

### 2️⃣ 任务二：智能体会做 `.docx` / `.xlsx` / `.pptx`（全程离线）

说一句「做个新品发布会的排期表」「写份活动执行方案」「做个产品介绍 PPT」，智能体就**产出一个真文件**，不再是聊天里的一段字。

**用了哪三个库**（都在项目 `.venv` 里，已钉进 `requirements.txt`，随便携包整个 `.venv` 一起带走，用户不需要 `pip install`）：

| 格式 | 库 | 版本 |
|------|----|------|
| `.docx` | `python-docx` | 1.2.0 |
| `.xlsx` | `openpyxl` | 3.1.5（连带 `et-xmlfile` 2.0.0、`XlsxWriter` 3.3.1 也在 .venv 里） |
| `.pptx` | `python-pptx` | 1.0.2（连带 `lxml` 6.1.3、`Pillow` 13.0.0） |

三个库都是**纯本地写 OOXML，运行期一行都不联网**（护栏测试直接扫 `app/agent/documents.py` 源码，出现 `urllib` / `requests` / `socket` / `http.client` / `httpx` / `ftp` 或 `http://` 就红）。构建期一次性装库不算运行期联网。

链路（只借聊天那条通道，不排队、不碰显存、不碰五个生成模块）：

1. `router.py` 新增 `document` 通道：`DOCUMENT_WORDS` 三族词（含中文长词如「排期表 / 对照表 / 会议纪要 / 演示文稿」，**刻意不含裸「表」**）、必须同时有「做/写/列/整理」这类动词；三种情况不算要文件——有垫图且像改图、话里有「怎么/什么是」（问答）、媒体词比文档词更早出现（「画一张海报，主题新品发布方案」还是文生图）。`# 3b)` 再加一条：硬问法（怎么做 / 如何 / 什么是）+ 文档词 + 无媒体词 → 走 chat，**不会**再顺手画一张莫名其妙的图。
2. `service.py::_write_document()`：先 `_ensure_local_llm(wait_seconds=90)`（模型没就位就按题目搭骨架、`degraded=True`，不编内容）→ 让本机模型按「只准输出这种结构」的系统提示写 → `parse_outline()` / `parse_table()` 解析 → 空输出退 `skeleton_plan()` 并如实说明。
3. `documents.py` 用上面三个库落盘：docx＝大标题＋导语＋Heading 1＋List Bullet；xlsx＝表头加粗＋`freeze_panes="A2"`＋列宽按 CJK 两格估＋备注在数据下方；pptx＝封面页＋每小节一页（`Pt(20)`）＋备注进演讲者备注。文件名 `主题-20261107-2148.docx`，重名自动 `-1` **绝不覆盖**。

**文件落在哪（与图片/视频/音乐同一套规则，一处都不特殊）**：写进「输出目录」`config.output_dir`（当前 `D:\ChenguAI\Output`，与网页端 `/outputs/` 严格同源），再按生成模块同一份 `app/core/artifacts.py::copy_artifacts()` 镜像到**项目内 `output\`** 和**设置里的「自动另存目录」**（改完即时生效，重名同样 `-1`）；拿不到 `TaskService` 槽位的独立跑法会退回默认的项目内 `output\`。回复里把每个位置都写出来。

**两端一致**：

- 网页端（`app/web/static/agent.js` + `app/web/server.py`）：气泡里给文件卡片（文件名、格式、行数/页数、大小），点一下下载（`/outputs/<名>?download=1`）；`_MEDIA_TYPES` 补了三种 OOXML 的标准 MIME，浏览器才知道是 Word/Excel/PPT。静态资源版本号升到 `?v=20261107e`（浏览器自动重取新脚本，不必手动清缓存）。
- 桌面端（`app/ui/pages/agent_page.py`）：同一份数据渲染成文件卡片 + 「打开」「打开所在文件夹」两个按钮（`QDesktopServices`，装了 Word/WPS/Office 就直接能改），镜像位置逐条列出。

**三种格式各测过一个没有**——测过，两层都测：

- 离线冒烟（假模型输出 + 真库）：规整输出 / 散文输出 / 空输出 三种输入 × 三种格式 + 三份骨架，共 **10 个文件全部生成成功**（docx ≈37 KB、pptx ≈29–44 KB、xlsx ≈5.1–5.5 KB），并用同一批库读回校验：docx 标题与小节顺序对、pptx 封面 + 小节页数量对（不多出空标题页）、xlsx `A1:F6`（排期表 5 行 × 6 列）与散文退化的两列都对。
- 自动化测试：新增 `tests/test_agent_documents.py` **19 条**（意图判定 8 条含「不被媒体词抢走」「问法不算文件」；三格式落盘＋读回 7 条；服务层落点与镜像 4 条；网页 MIME 1 条）。全量 `pytest tests -q` → **103 passed, 1 skipped**（上一轮 84 passed, 1 skipped ＋ 本轮 19 条）；`node --check agent.js / app.js` 通过；`compileall` 通过。
- 桌面端另外用离屏渲染（`QT_QPA_PLATFORM=offscreen`）跑了一次真控件探针：只有文件、没有正文也会出卡片，卡片文字 `📄 文件名 / 格式 · 行数×列数 · 大小`，鼠标悬停是完整路径，下面确实有「打开」「打开所在文件夹」两个按钮，镜像目录逐行列出——桌面端这一侧不用真机也已验证。

### 等待人工真机验收（这一步只能你在真机上看）

1. **两端都要重启**才吃新代码（网页端 `scripts\run_web.ps1 -Restart`，浏览器 Ctrl+F5；桌面端关掉旧窗口再 `scripts\run_app.ps1`）。
2. 说一句「做个新品上市的排期表」→ 应回「已生成 Excel 表格（.xlsx）」＋ 行数×列数 ＋ 存放位置；`D:\ChenguAI\Output` 和项目根 `output\` 各出现同一个文件名，设了自动另存目录的话那里也有一份，重名会加 `-1`。
3. 网页端点文件卡片能下载、能双击打开；桌面端「打开」能用系统关联程序打开、「打开所在文件夹」能跳到输出目录。
4. 换「写份活动执行方案」（Word）和「做个产品介绍 PPT」（PPT）各来一句；说「怎么做一份 PPT」应该是**问答**，不该出文件也不该出图。
5. 内容质量由本机 Qwen3-8B 决定；模型还没就位时会给「只有骨架、正文标了（待补充）」的文件并在 notes 里说明——这不是 bug，是如实降级。
6. **本轮同样没跑任何 GPU 生成**，测试里的模型全是桩；真实第一句请你亲口说。任务一的显存数字要达到 3686 MiB，需要你先关掉那个占 10 GB 显存的第三方程序（我们不会替你关）。


---

## 🩹 截图点名的三件事：不拿闲聊当生成需求 / 智能体文生图一次出 4 张 / 退出与空闲把显存真的还回来（2026-11-08 · 开发完成，等待人工真机验收）（历史：SD1.5 时代的口径，**文生图已于 2026-11-10 下架换成 Sana，且改为一次 1 张**——「4 张」这条已作废；「闲聊不派任务」「退出/空闲交还显存」两条仍然有效）

用户给的截图（`WPS拼图0.png`）里三处红笔标注就是本轮的全部范围：`输入你好，直接生图？`、`第3张没明白意图是画…一句话→1张图`、`赛博朋克雨夜街头…就设置了生成参数`。**只动「AI 智能体」的意图判定、任务批量、以及任务调度/显存判定这三层**；文生图、图生图、文生视频、图生视频、AI 音乐各自的模型与生成逻辑一行没动，自带大模型的加载参数（`ctx 16384` / KV `q8_0` / `--fit-target 3686`）也一行没改。

### 1️⃣ 「输入你好，直接生图？」——判不出生成/文档/文案意图就按聊天回答，绝不兜底成文生图

- **根因**（`app/agent/router.py`）：路由最后有一条「有描述就当提示词 → 文生图」的兜底，所以一句没有明确说法的话（甚至「你好」）也可能被派成出图，而且还会顺手「设置生成参数」——截图里那种莫名其妙就是这么来的。
- **改法**：把兜底整条删掉，只认三种明确说法：
  1. 强动词：绘制 / 绘画 / 渲染 / 出图 / `draw` / `paint` / `sketch` / `render` / `illustrate`；
  2. 「画」＋量词（正则 `_DRAW_WITH_COUNTER`）：画一张、画两幅、画一只柴犬、画个头像……
  3. 软动词＋量词：生成/创作/制作/来一张/给我/帮我/generate/create/make，且带「一张/一组/4 张」这类量词。
- 三条都不沾 → **按聊天回答**，notes 里如实说明为什么：`这句话没有画/生成这类明确说法，按聊天回答；要画面请说「画一张…」或「生成一张…」`（像是在问东西则写「这句话像是在问东西」）。纯描述词（`赛博朋克雨夜街头，电影感，冷色调`）现在是**聊天内容**，不是自动任务；要说「画出来」才走文生图。
- `HELP_TEXT` 补两行：明确说法才画；文生图一次出 4 张。
- 新增测试 4 条（`tests/test_agent_and_i18n.py`）：闲聊不成图 / 纯描述不成图 / 明确「画一张…」照旧出图 / 视频·音乐信号不被踩。

### 2️⃣ 「一句话 → 1 张图」——智能体文生图一次出 4 张，跟手动生成那一栏完全一致

- `app/agent/service.py::IMAGE_BATCH_SIZE = 4`：一句「画一只打瞌睡的柴犬」提交 **4 条 `image` 任务**，走的是与网页端/桌面端手动出图同一条 FIFO 队列、同一个引擎（不是另写一套生成逻辑）。
- **种子怎么给**（`_rand_seed()`）：用户明确给了种子就锁住，四张连号（123 / 124 / 125 / 126，方便对比挑一张）；没给就四张各用随机种子，画面互不相同。回执写清是哪一种：`（种子锁定 123，四张连号 123~126）` 或 `（四张各用随机种子，画面互不相同）`。
- 提交失败的条目如实列出（`error_tail`），不假装成功；回执带上 `（任务 xxxxxxxx 起）`。
- **视频与音乐仍是一次 1 个**：12GB 级显存的模型不允许一次压四个任务，这两类 `batch_size` 仍是 1。
- 两端 UI 本来就支持一次多任务（`app/ui/pages/agent_page.py:366`、`app/web/static/agent.js:320`），前端一行没改就能看到四张。
- 新增测试 3 条：出 4 条任务 / 种子连号 `[123,124,125,126]` / 视频·音乐仍 1 条。

### 3️⃣ 显存要真的还回来：退出不留孤儿 → 空闲 10 分钟自动卸载 → 判定不再拿 nvidia-smi 的「已用」当真

**(a) 孤儿进程的根因与三层护栏。** worker 生成途中卡在 `generate` 里读不到 stdin 的 EOF，进程被强杀时 `finally` 根本不跑 → 它拉起的 `llama-server.exe` 没人停，显存就一直挂着。新模块 `app/core/process_guard.py`（纯标准库）三层兜住：

1. **内核层**：Windows 作业对象 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`（`assign_pid_to_kill_job`）——父进程一消失、句柄一关，内核连带杀掉 worker 与 llama-server，不给孤儿机会；
2. **自查层**：worker 里的父进程看门狗（`start_parent_watchdog`，靠环境变量 `CHENGUAI_PARENT_PID`，2 秒一次 `GetExitCodeProcess`，退出码不再是 259 就 `os._exit(0)`）；
3. **补扫层**：下次启动按镜像名清扫「父进程已死」的自家孤儿（`orphan_pids_by_image`，只认 `llama-server.exe` 这类自家镜像名，**绝不碰别人家的程序**）。

挂点：`app/tasks/process_adapter.py::_ensure_process`（每个 worker 一律带 env ＋进作业对象）、`app/tasks/process_worker.py::main()`（看门狗；五个 `*_worker.py` 都跑在同一宿主进程 ⇒ 一处覆盖全部引擎）、`app/agent/local_server.py::ensure_running()`（llama-server 也在作业对象里）、桌面端 `app/bootstrap.py`（`aboutToQuit → window.shutdown_services()`；启动时 `window.sweep_orphan_llm()`）、网页端 `app/web/server.py::run_web_server()`（启动后清扫；`finally` 里 `task_service.shutdown()` 之后 `stop_llm_for_exit()`）。

**(b) 引擎空闲 10 分钟自动卸载（`app/tasks/service.py`）。** `ENGINE_IDLE_LIMIT_SECONDS = 600.0`、`IDLE_REAPER_POLL_SECONDS = 30.0`；`_idle_reaper_loop` 每 30 秒看一眼：本进程有任务在跑、或队列里还有货（可能是另一个进程刚提交的）就把空闲时钟拨回去——**任务中途绝不拆台**；真到 10 分钟才 `_reap_idle()`：先 `engine_manager.release_idle_engines(None)` 把本端自己拉起的空闲引擎 worker 全停掉（显存当场归还），再依次调用注册进来的钩子 `AgentService.reap_idle_llm()` → `local_llm.stop_if_idle()` 停掉自带 llama-server 并作废端点缓存。llama.cpp b12182 没有 `/unload`，交还显存只能停自家进程，所以日志与回执都写明白：`自带大模型空闲 N 分钟，已自动卸载退出（显存归还给生成引擎）；下次聊天会自动重载权重。`（重载约 15 秒）。

**(c) 显存判定按进程实测，先自己让路再拦。** Windows 下 nvidia-smi 的 `used` 会把驱动保留的、以及已经结束但还没回收的进程都算进去，明显偏大（本机实测 total 12288 / free 1420 / used 10694，而按进程实测别人真正占着 10110 MiB ⇒ 还能用的只有 2169 MiB）。`app/core/hardware.py` 新增 `process_vram_holders()`（PowerShell `Get-Counter "\GPU Process Memory(*)\Local Usage"` ＋ `Get-CimInstance` 认进程名；`nvidia-smi --query-compute-apps=...used_memory` 在 Windows 全回 `N/A`，不能用）、`effective_free_mib()`、`holders_summary()`、带 20 秒缓存的 `vram_holders_cached()`（同步实测一次几百毫秒，所以只在拦任务那条路径上调用）。

门槛改成两步：网页端 `app/web/static/app.js::vramFloorCheck()` 与桌面端 `app/ui/pages/creation_page.py::_vram_floor_check()` 先按实测空闲判；不够就先让本端把自己拉起的空闲引擎释放掉（新端点 `POST /api/engines/release-idle` → `TaskService.release_all_idle_engines()`；桌面端用 `run_coroutine_threadsafe` 调同一个函数），清完再测一次；**还不够才提示，并且点名是哪个程序占着显存**（本机就是第三方 `strata.exe` 约 10081 MiB——我们不会替你关，也不会去动它）。两端引擎说明里原来那句「出图前先双击 `scripts\release_local_engine.bat`」已改成「本端会先自己让路，仍不够会点名占显存的程序」，手动脚本作为可选项保留（两端逐字一致，脚本比对过）。

- 新增测试：`tests/test_resource_hygiene.py` 13 条（`effective_free_mib` 实测算法与退回分支／`ours` 排除自己人／holders 文案三档／缓存复用与强制重测／llama-server 空闲卸载四种分支——外部端点与没到时间都不许动手／进程存活与孤儿镜像名／网页端 `/api/hardware` 的 `detailed` 要能穿过线程池——就是这里曾把参数按位置传，整个端点直接 500）、`tests/test_agent_and_i18n.py` 2 条（空闲会卸自己的模型并作废端点缓存／会把自己注册进收割钩子）、`tests/test_task_service.py` 2 条（收割先停引擎再叫钩子／任务在跑时绝不动手）。

### 复核结果（自动化部分；本轮同样没跑任何 GPU 生成）

- 全量 `pytest tests -q --basetemp=.tmp_pt` → **127 passed, 1 skipped in 33.08s**（上一轮 103 passed, 1 skipped ＋ 本轮新增 24 条）。
- `node --check app/web/static/app.js` / `agent.js` 通过；`python -m compileall -q app tests` 通过。
- 两端引擎说明逐字一致（脚本比对 `param_panel.ENGINE_MODE_HINTS` 与 `app.js` 三处字面量），路径反斜杠按 JS 规则写成 `scripts\\release_local_engine.bat`（显示出来是一个 `\`）。
- 网页端静态资源版本号升到 `?v=20261108a`（浏览器自动重取新脚本，不必手动清缓存）。
- 护栏只认自家镜像名：真机探针 `orphan_pids_by_image("chenguai-不存在的镜像名.exe") == ()`，全程没误杀任何用户程序（占 10 GB 显存的第三方程序一直原地没动）。
- 网页端重启后真机冒烟（不提交任何生成任务）：`GET /api/hardware?detailed=1` 给出 nvidia-smi 的 free 1312／used 10802（虚高，只当参考），按进程实测真正可用 2132MB，并点名 `strata.exe 10081MB`、Notepad、TextInputHost（第三方程序一律不动）；默认轮询路径不带点名字段，不去起 PowerShell；`POST /api/engines/release-idle` 返回 `released: []`（当时本端没有常驻引擎可停），显存数字照样现查。

### 等待人工真机验收（这一步只能你在真机上看）

1. **两端都要重启**才吃新代码：网页端 `scripts\run_web.ps1 -Restart`（浏览器 Ctrl+F5），桌面端关掉旧窗口再 `scripts\run_app.ps1`。
2. 说「你好」「在吗」「今天有什么安排」→ 只应该聊天回答，notes 说明为什么没派任务；再说「画一张赛博朋克雨夜街头的柴犬，电影感」→ 应该**一次出 4 张**，任务编号连排，回执写清种子（锁定连号 / 各自随机）。
3. 纯描述词（不带动词的说法）现在不该自动生成图片；要画就带上「画一张 / 生成一张」。
4. 显存归还：聊天与生成都停下来等 10 分钟，`logs\` 里应出现「空闲 10 分钟没有任务：显存已交还（引擎 worker：…；自带大模型空闲 N 分钟，已自动卸载退出…）」，任务管理器里 `llama-server.exe` 与本端引擎 worker 的 python 进程都应消失，`nvidia-smi` 显存回落。
5. 退出清理：关掉网页控制台窗口（或 Ctrl+C）后，它拉起的 worker 与 llama-server 应一并消失；桌面端关窗口/退出也一样。中途硬杀（任务管理器结束进程）也应被内核作业对象连带收掉；万一还有残留，下次启动会自己扫掉。
6. 提交视频/音乐时若显存确实不够，提示里应点名是哪个程序占着（本机就是 `strata.exe`），而不是叫你去双击脚本；「先自己让路」那一步会额外说明本端释放了哪些引擎。
7. 想立刻把显存腾干净：网页端提交视频/音乐前会自动做；要手动也可以继续跑 `scripts\release_local_engine.bat`。

---

## 🧊 2026-11-08 深夜：FLUX 出局、SD1.5 + RealVis 接手，三条铁律定档（开发完成，六能力真机验收进行中）（**历史章节**：SD1.5 这一代**已于 2026-11-10 下架**、退到未注册后备，现役文生图是 Sana-1.6B 多语言版，见上面「文生图引擎 Sana-1.6B 多语言版」一节；本节里 FLUX/SD1.5 的实测数字与选型理由作为审计证据保留，不得当成现状引用）

### 1️⃣ 为什么必须换掉 FLUX

FLUX.1-schnell 的权重是 **23.7 GB（磁盘 31.41 GB）**，FP8 化后也要 12 GB 以上显存，硬塞进 RTX 3060 12GB：只要本机还有别的程序占着显存（第三方推理程序常占 9-10 GB），出图必然爆显存。2026-11-08 用户明确要求「删除 FLUX，重新下载峰值不超过 6GB 的模型」，并定下三条铁律：

> **① 所有大模型必须可商用；② 中英文 prompt 都要能用；③ 运行峰值显存不超过 6GB。**

### 2️⃣ 新的图像三件套（全部可商用、全部离线、全部实测达标）

| 组件 | 上游 | 许可 | 体积 |
|------|------|------|------|
| 主模型 RealVis V5.1（SD1.5 精调） | `SG161222/Realistic_Vision_V5.1_noVAE` | OpenRAIL-M（可商用） | 3.97 GB |
| VAE | `stabilityai/sd-vae-ft-mse` | MIT | 0.31 GB |
| LCM-LoRA 加速 | `latent-consistency/lcm-lora-sdv1-5` | OpenRAIL++ | 0.13 GB |

- 下载脚本 `scripts\download_image_weights.py`（走 `hf-mirror.com` 镜像、断点续传、按仓库分别校验文件体积；LoRA 的体积下限单独定档，不再被误判成「没下全」）。
- 引擎 id 两端改名：文生图 **`sd-realvis`** / 图生图 **`sd-realvis-i2i`**（同一份权重、两个独立 worker 进程、互斥加载，绝不双占显存）。
- **已删除的陈旧内容**：FLUX 权重 31.41 GB（D 盘可用 138.85 GB → **170.26 GB**）、`app/engine/image/flux.py`、`app/tasks/flux_worker.py`、`scripts/acceptance_flux.ps1`、`scripts/probe_flux_encode.py`、`scripts/probe_flux_tokenizer.py`、`tests/test_flux_image.py`、`tests/test_flux_worker_adapter.py`，以及 i18n 里再没人引用的 FLUX 步数提示 key（`source-zh.txt` + 17 个语言包各删 1 条）。

### 3️⃣ 真机实测（铁律是否达标，只看这张表）

| 能力 | 实际参数 | 结果 | 本进程显存峰值 |
|------|----------|------|----------------|
| 文生图 | 512×512、6 步、LCM 开、model 卸载 | 6.1 s 出图 ✅ | **1884 MB** |
| 文生图（真 1080p） | 目标 1920×1080 → 底图 896×512 + **15 块**分块高清重绘 | 58.5 s ✅ | **2051 MB** |
| 文生视频 | Wan2.1-1.3B，512×384、49 帧、12 步、**逐层卸载** | 378.7 s（含加载）出 3.06 s / 16fps mp4 ✅ | **2021 MB** |

三个模态都远在 6 GB 红线以内。**视频默认 28 步 ≈ 14 分钟**（1.3B 模型 + 逐层卸载换显存的代价），已写进界面提示，不粉饰耗时。

### 4️⃣ 中文提示词为什么能出图（以及它的边界）

SD1.5 的文本编码器是 CLIP，词表里**没有中日韩**——纯中文提示词会得到「有画面、没主体」的图。新增 `app/engine/prompt_bilingual.py`（纯标准库、零显存、完全离线）：

- 中文描述词 → 英文标签，**按词表最长优先匹配**（先试 4/3/2 字词组，再看连接词），所以「中国风」「汉服」不会被单字拆坏；
- 词表没收录的词原样保留，并把未命中的词**如实打出来**（worker 日志与任务 metadata 里都能看到 `prompt_unmapped`）；
- 原文一字不丢：产物 metadata 记录 `prompt_original` / `prompt_effective` / `prompt_translated` / `prompt_unmapped`，界面上仍然显示用户写的中文。

A/B 实测：`一只在窗台上的橘猫，自然光，照片质感，细节丰富` —— 桥接前＝没有主体的空街景；桥接后＝窗台上的橘猫、自然光、写实风格 ✅
视频（Wan 用 UMT5 多语编码器）、音乐（ACE-Step 多语）本身原生支持中文，**不做桥接**，Wan 的默认负向提示词本来就是中文。

### 5️⃣ LCM 加速的代价（诚实说明，不藏着）

LCM-LoRA 档默认 `guidance_scale = 1.0`，这时 **CFG 被跳过，负向提示词完全不参与引导**。为了让「不要模糊、不要多余手指」这类负向词真的起作用：填了负向提示词就自动抬到 **2.0（LCM-CFG）**，代价是**约慢一倍**。界面提示、README、代码注释三处都写明了。

### 6️⃣ 「1080p」在这里的真实含义

SD1.5 是 512 像素档的模型，原生直出超过约 **0.85 MP** 会崩构图（人多了手就坏）。所以超过约 0.85 MP 的画幅（含 1920×1080、2048×1152）自动走：

> 底图（≤0.46 MP）→ Lanczos 放大 → 512×512 分块重绘（重叠 128 px、正弦羽化缝合）→ 真 1920×1080

只用 SD1.5 自己的权重，**不引入任何非商用超分模型**（Real-ESRGAN 官方权重禁止商用，已排除）。面板按像素选择：512×512 / 1024×576 / 576×1024 / 1344×768 / 1920×1080 / 2048×1152，超过约 0.85 MP 会写明「约 N 块，峰值显存不变、耗时按块数增加」。

**真 1080p 视频在 6 GB 内做不到**（Wan2.1-1.3B 是 480p 档），已提前说明。视频引擎按 **token 预算（≤20000 tokens = latent 帧数 × 宽/16 × 高/16）**判定你选的档位装不装得下：装得下就原样出片，装不下就**在提交前拒绝**，并给出具体数字——「保持分辨率减帧：这一档最长 N 帧 / N 秒」或「保持帧数降尺寸：这个帧数下最大 W×H」，由你自己选一项。**不自动降档，不偷改帧数，也不会只往 stderr 写一行就悄悄缩画幅。**

### 7️⃣ 桌面端与网页端本次同步点（必须一致）

- 引擎 id 全部换成 `sd-realvis` / `sd-realvis-i2i`，两端逐字一致；
- 参数白名单一致（图像端新增 `hires`，可强制开/关分块重绘），高级参数提示文案两端一致；
- 滑块一致：图像宽高 256-2048 step 8、步数 **1-12 默认 6**；视频宽高 256-1024 step 16、步数 1-50 默认 28、帧数 5-161；
- 六个比例 chip 与像素上限（850 000 px）两端一致，分块数估算算法两端等价（桌面端 `.venv` 没有 numpy，所以是等价的独立实现并有测试对拍）；
- 视频显存门槛两端同步：最低显存 **6144 MB** / 提交前空闲门槛 **2000 MB**（旧的 12288 / 10500 作废，那是 FLUX 时代的口径）。

### 8️⃣ 测试与静态检查（本轮）

- 全量回归（`.venv`）：**183 passed / 10 skipped**（跳过的全是需要 numpy/torch 的分支，诚实跳过而不是假通过）。
- 同一批用例在 `.venv-image`（有 numpy/torch、无 PySide6）再跑一遍：**69 passed / 6 skipped**，见下面第 🔟 节的双环境测试矩阵。
- 新增：`tests/test_sd15_plan.py`（画幅取整、单遍/分块预算、1080p 走分块）、`tests/test_hires_tiling.py`（分块网格、羽化掩膜、融合几何）、`tests/test_sd_worker_adapter.py`（worker 参数白名单、metadata 字段回传、shutdown 引用清理）。
- 新增 `tests/test_prompt_bilingual.py`（9 条中文桥接回归：英文直通、常用词命中、补录词全部命中、「中国风」不被单字拆碎、未收录词如实上报、原文保留、幂等、空输入）；`tests/test_sd15_plan.py` 的 `app.ui` import 改为可选并用 `@_needs_param_panel` 标注 4 个两端对拍用例；`tests/test_hires_tiling.py` 修掉两处只在有 numpy 时才会执行到的缺陷（RGB 图上 `getpixel(...) < 10` 比的是元组 → 改比 `[0]` 通道；Pillow 弃用的 `getdata()` → `tobytes()`）。旧 FLUX 测试两份已删；`scripts/verify_env.py`、`scripts/build_portable.ps1`、`scripts/smoke_desktop_generation.py`、`scripts/download_video_weights.py` 的模型清单与文案已同步改名。
- 修掉的真实缺陷：diffusers 0.41 里没有 `StableDiffusionPipeline.enable_vae_tiling`（改到 `pipe.vae.enable_tiling()`）；`app/engine/abstract.py` 里多余的 `import torch`（导致 `.venv` 连 `app.engine.*` 都导入不了）；Wan 出帧判空 `not frames_out[0]` 在 numpy 数组上抛「truth value is ambiguous」（改成只看批次长度，mp4 才写得出来）。

### 9️⃣ 交付前还差的（不隐瞒）

- **图转视频、AI 音乐的真机端到端验收同时需要空闲显存和空闲物理内存**（图生图本轮已真机通过，见第 3 节实测表）：2026-11-08 深夜实测本机空闲显存 1338/12288MB、空闲物理内存 2.1/63.8GB，元凶是 `strata.exe`（RSS **46.6GB**、占显存 10776MB）；视频/音乐提交前会被两端预检诚实拦下。要跑这两项请先关闭占显存的第三方程序，或点控制台/`scripts\release_local_engine.bat` 的「释放空闲引擎」（只结束**本项目自己**的引擎进程，绝不杀第三方程序；本地大模型空闲 10 分钟也会自动卸载）。
- 网页端重启后的冒烟、桌面端真机点击验收仍需在真机上看。


### 🔟 验收入口、两端冒烟与双环境测试矩阵（2026-11-08 深夜补）

**真机验收脚本**（全部离线、产物落 `output\`，产物不合格就不返回 0，绝不伪造）：

| 入口 | 做什么 | 显存门槛 |
| --- | --- | --- |
| `scripts\acceptance_sd.bat`（→ `.ps1` → `acceptance_sd.py`） | 文生图 1024×576（steps 6、seed 20261109）＋ 以这张图为垫图的图生图（strength 0.55），校验 PNG ≥50KB | 空闲显存 ≥1500MB（可 `--min-free` 调）**且空闲内存 ≥8000MB**；`.ps1` 每 180s 读一次 `nvidia-smi` 与 `Win32_OperatingSystem`，最多等 40 轮（`-MaxRounds`） |
| `scripts\acceptance_video.bat`（→ `.ps1` → `acceptance_video.py`） | 文生视频 512×384 / 33 帧 / 12 步 ＋ 图转视频（首帧自动挑 `output\acceptance_sd` 里最新的 PNG），校验 mp4 ≥50KB | 空闲显存 ≥4000MB **且空闲内存 ≥16000MB**（`.ps1` 两个条件同时满足才启动；`-MaxRounds`/`-MinFreeRAMMB`，别写 `-Rounds`）；`--skip-i2v` 只验文生视频 |
| `scripts\acceptance_music.bat`（→ `.ps1`，参数 `-MinFreeVRAMMB` / `-MinFreeRAMGB`） | ACE-Step 中文人声端到端（48kHz wav、时长截断实测），经网页控制台提交、走同一条队列 | **空闲显存 ≥12300MB**（12GB 几乎要全空）**且空闲内存 ≥36GB** |
| `scripts\acceptance_chain.ps1` | 按 **图片 → 视频 → 音乐** 顺序串起来自动等显存窗口，日志落 `logs\` | 逐步各自预检 |

空闲显存不够时这些脚本 **exit 2 并明确提示先释放显存**，不会硬着头皮 OOM。释放显存用 `scripts\release_local_engine.bat`：它只结束 `%LOCALAPPDATA%\Strata\components\` 下**本项目自己的**本地引擎进程（`strata.exe` / `strata-vision.exe`），明确不碰控制端、网页控制台和 python，也绝不杀无关的第三方程序；本地大模型空闲 10 分钟同样会自动卸载显存。

**六能力真机验收状态（2026-11-08 收尾时点）**：

| 能力 | 状态 | 实测证据 |
| --- | --- | --- |
| AI 智能体 | ✅ 已通过 | 项目内 `tools\llama_cpp` + `models\llm\gguf\Qwen3-8B-Q4_K_M.gguf`，只绑 127.0.0.1:11435 |
| 文生图 | ✅ 已通过 | 512×512 → 6.1s、峰值 **1884MB**；1920×1080（底图 896×512 + 15 块分块重绘）→ 58.5s、峰值 **2051MB** |
| 图生图 | ✅ 已通过（本轮补测） | 768×512、strength 0.55、6 步 → 18.6s、峰值 **2094MB**，产物 `output\smoke_sd_i2i\sd_img2img_20261108_163509.png` |
| 文生视频 | ✅ 已通过 | 49 帧 @512×384、12 步、seed 7 → 378.7s（含加载 65s）、峰值 **2021MB**（6GB 铁律达标），产物 `output\smoke_wan\wan_t2v_20261108_161619.mp4` |
| 图转视频（VACE） | ⏳ 代码就绪、待「显存＋内存」双窗口重跑 | 真机尝试在加载 checkpoint（2 分片中的第 1 个）后进程凭空消失、**零 Python traceback**；同轮实测空闲物理内存只剩 2.5GB（后复测 2.1GB），元凶 `strata.exe` RSS 46.6GB —— 是**内存不足把原生进程杀掉**，不是代码路径被证伪。已加加载前内存闸门（空闲 <16GB 直接给可操作的中文错误），`acceptance_video.py` 的 i2v 分支就绪，等资源窗口重跑，**不假装通过** |
| AI 音乐 | ⏳ 待显存＋内存 | 引擎侧门槛：总显存 ≥12288MB（RTX 3060 达标）、空闲显存 ≥6000MB、空闲内存 ≥14336MB；验收脚本更保守：空闲显存 ≥12300MB 且空闲内存 ≥36GB；本轮实测空闲显存 1338MB、空闲内存 2.1GB，两端预检会诚实拦下并提示跑 `release_local_engine.bat` |

**两端冒烟（本轮）**：

- 网页控制台重启后：`GET /api/engines` 恰好返回五个引擎 `sd-realvis` / `sd-realvis-i2i` / `wan-t2v` / `wan-i2v` / `ace-step-15`（FLUX 已不存在）；`GET /api/health` 正常；`GET /api/hardware` 回显卡与内存快照（含 `memory_free_effective_mb`）。
- 桌面端 `python -m app.bootstrap` 启动后连续运行 25 秒无异常退出，只有一条 Qt 自带的 `QFont::setPointSize: Point size <= 0` 告警（界面字号在 `theme.py` 里全部是 px 定义，与本项目代码无关，不影响显示）。
- 静态检查：`node --check app\web\static\app.js`、`node --check app\web\static\agent.js` 均 0；`compileall app scripts tests` 均 0。

**双环境测试矩阵**（同一份测试在两个 venv 各跑一遍，各取所长，缺什么就明说什么跳过）：

- `.venv`（有 PySide6，无 numpy/torch）：全量 **183 passed / 10 skipped**，两端面板对拍用例在此环境下是**真实执行**的。
- `.venv-image`（有 numpy/torch，无 PySide6）：`test_sd15_plan.py + test_hires_tiling.py + test_sd_worker_adapter.py + test_prompt_bilingual.py` → **69 passed / 6 skipped**，跳过的只有 4+2 个「读桌面端面板源码/常量」的对拍用例（`pytest.importorskip("PySide6")`），换来的是分块融合的真实覆盖：`TileBlender` 的羽化权重、掩膜paste、块超出画布报错、结果尺寸与亮度分布都在这里被真正执行过。


### 1️⃣1️⃣ 同一轮收尾顺手修掉的四类「口径不一致 / 脚本会假报错」（2026-11-08 深夜）

- **视频的显存口径两端逐字统一成 6GB**。桌面端 `app/ui/widgets/param_panel.py` 与网页端 `app/web/static/app.js` 里还有三处提示（帧数偏紧、像素说明、步数提示）和两处报错文案（Wan2.1 启动失败时点名的「最低要求」）写着 12GB，而门槛常量早就是 **6144MB 总量 / 2000MB 空闲**——这是会直接误导用户的文案。现在两端逐字一致，措辞也从「CPU offload」改成引擎实际做的「逐层 CPU 卸载」。`app/web/static/index.html` 的脚本缓存版本号 **20261108c → 20261108d**（不改版本号浏览器会继续用旧 app.js）。i18n 同步：`app/web/static/i18n/source-zh.txt` 的 key 与 **17 个 locale**（ar/de/en/es/fr/hi/id/it/ja/ko/nl/pl/pt/ru/th/tr/vi）里对应的译文一起改成 6GB；**音乐模块自己的 12GB 门槛不动**（ACE-Step 的真实要求就是 ≥12288MB 显存 / ≥7000MB 空闲）。
- `scripts/verify_env.py` 假报错：以前把**可选项 xformers** 计进必需组件，自检结果 6/7 → exit 1；而且建议安装命令给的是 **cu118** 轮子还带 xformers（本项目是 **cu130**，图像/视频引擎走 diffusers 的 SDPA，实际没装 xformers 也照样出图出视频）。现在必需项与可选项分开统计（可选项缺失不影响退出码），安装建议换成本项目四个 venv 的真实命令；`.venv-image` 里复跑：**必需项 5/5，exit 0** ✅。
- `scripts/download_image_weights.py -VerifyOnly` 以前**这个模式根本不存在**（参数被忽略，脚本照样连 HF 列文件；桌面端 `.venv` 没装 huggingface_hub，直接 ModuleNotFoundError，exit 1）。现在 ① huggingface_hub 改成只在下载路径里懒 import；② 真正实现了离线校验，并且「快照算不算完整」的判定与引擎侧 `app/engine/image/sd15.py` 的 probe **一一对应**（模型快照要 `model_index.json`、VAE 看权重文件、LoRA 认 `lora_weights.safetensors`；以前一律要求 `model_index.json`，会把已经就位的 VAE 误报成「缺快照」）。实测在 `.venv` 里离线跑：三件套全部 **VERIFY OK**（RealVis 3.97GB / VAE 319MB / LCM-LoRA 129MB，合计 4.41GB），**exit 0** ✅。
- 测试里两个只有换 venv 才会暴露的真实缺陷：`tests/test_hires_tiling.py` 在 RGB 图上写 `getpixel(...) < 10`（元组和整数比较，TypeError）→ 改成比 `[0]` 通道，Pillow 弃用的 `getdata()` → `tobytes()`；`tests/test_sd15_plan.py` 对 `app.ui` 的顶层 import 改成可选并用 `@_needs_param_panel` 标注，使同一份用例在 `.venv`（有 PySide6）与 `.venv-image`（有 numpy）两边都能跑到各自应有的覆盖。
- 冒烟复核：桌面端 `python -m app.bootstrap` 连续运行 25 秒无异常退出；网页控制台重启后 `GET /api/engines` 恰好 5 个引擎、`GET /api/hardware` 正常回显卡快照；`node --check app/web/static/app.js`、`compileall app scripts tests` 均为 0。


---

## 🧠 2026-11-08 深夜二段：图转视频「进程凭空消失」的真相、内存闸门、四套模式皮肤（开发完成）

### 1️⃣2️⃣ 缺的不只是显存——是**空闲物理内存**（这条是本轮最重要的发现，之前一直查错方向）

**现象**：`wan-i2v`（VACE）真机尝试在 `checkpoint shards: 50%|█████ | 1/2` 之后进程直接没了，日志里
**没有任何 Python traceback**，只有 HF offline 警告；上一轮的结论是「显存被第三方程序挤爆」。

**本轮实测把结论翻了过来**（`Get-CimInstance Win32_OperatingSystem` + `nvidia-smi` + `Get-Process`）：

| 实测项 | 数值（2026-11-08 深夜） |
| --- | --- |
| 显存 | 总 12288MB / 已用 10776MB / **空闲 1338MB** |
| 物理内存 | 总 63.8GB / **空闲 2.1-2.5GB** |
| 元凶 | `strata.exe`（`%LOCALAPPDATA%\Strata\components\engine\0.1.56-...-cuda13\`）RSS **46.6GB**、同时占显存约 10.7GB |
| 它的参数 | `--max-context 131072 --kv int8 --kv-resident 32768 --pool-workers 10 --vram-reserve-mib 1536` |

Wan2.1 的 **UMT5-XXL 文本编码器单件就约 11GB**，VACE 整套（UMT5 三分片 + transformer 二分片 + VAE）
加载需要约 16-18GB **物理内存**——权重要先从磁盘读进内存，再分片换入显存。空闲内存只剩 2.5GB 时，
Windows 直接把原生进程干掉，所以现场只留下「日志断在半路」。**这不是显存代码路径的问题，
而是我们从来没有检查过物理内存**——一个真实的缺陷，本轮修掉。

### 1️⃣3️⃣ 新增加载前内存闸门：三个引擎共用一个探针（`app/engine/memprobe.py`）

纯标准库（`ctypes` 调 `GlobalMemoryStatusEx`）、零依赖、四个 venv 与验收脚本都能 import；
非 Windows / API 失败时**不假拦截**（返回 `None` 就放行，让原生加载自己报错）。

| 引擎 | 空闲物理内存门槛 | 加载前还会检查（显存侧与其它前置条件） | 覆盖它的 env |
| --- | --- | --- | --- |
| 视频 `wan-t2v` / `wan-i2v` | **16384MB** | 逐层卸载需 ≥2000MB 空闲显存；总显存 ≥6144MB；离线权重快照必须齐全 | `CHENGUAI_WAN_MIN_FREE_RAM_MB` |
| 音乐 ACE-Step 1.5 | **14336MB** | 总显存 ≥12288MB；空闲显存 ≥6000MB；权重文件与代码仓库完整性 | `CHENGUAI_MUSIC_MIN_FREE_RAM_MB` |
| 图像（现役）`sana-multiling` | **16384MB**（加载闸门） | 空闲显存 <5120MB→自动退逐层换入、<1500MB→拒绝并点名占用者；`models\sana-multiling` 离线快照齐全（缺权重提示跑 `scripts\download_sana_weights.py`） | `CHENGUAI_SANA_MIN_FREE_RAM_MB` / `CHENGUAI_SANA_MIN_FREE_MB` |
| （未注册后备，2026-11-10 下架）图像 `sd-realvis` / `-i2i` | **6144MB** | 空闲显存 ≥3600MB→模型级卸载、≥1500MB→逐层卸载；三件套离线快照齐全 | `CHENGUAI_SD_MIN_FREE_RAM_MB` |

拦截文案是**可操作的中文**，不是「内存不足」四个字。真机实测（当前 1.1GB 空闲内存）：

```
Wan2.1 视频引擎 暂不能加载：系统空闲物理内存只有 1.1 GB（机器总内存 64 GB），
低于本引擎的加载门槛 16.0 GB：UMT5-XXL 文本编码单件就要约 11GB 物理内存。权重要先读进内存
再分片换入显存，内存不够时进程会被系统直接杀掉、连报错都没有（2026-11-08 的 wan-i2v 就是这么
静默消失的）。请先关闭占内存的程序（浏览器多标签页、其它 AI 工具、IDE）后重试；显存被本机推理
引擎占着时可双击 scripts
elease_local_engine.bat 释放。
```

视频产物 metadata 里也多了一项 `free_ram_mb_at_load`，事后台账能看出当时到底给了多少内存。

**验收脚本同步**：`acceptance_video.ps1` / `acceptance_image.ps1` 每一轮同时读空闲显存**和**空闲内存
（`Win32_OperatingSystem.FreePhysicalMemory`），两个门槛都满足才启动；`acceptance_video.py` 增加
`--min-ram`（默认 16000MB）。⚠️ 调用参数名是 `-MaxRounds` / `-MinFreeRAMMB`，写成 `-Rounds` 会被静默忽略。

### 1️⃣4️⃣ 顺手把两条与实测矛盾的老口径改齐（两端同步）

- `app/engine/video/wan.py`：CUDA OOM 报错仍写着「Wan2.1 编码阶段单件需要约 **11GB 空闲显存**」、
  `load()` 写着「需要 NVIDIA GPU（RTX 3060 **12GB** 级）」——与逐层卸载实测峰值 **2021MB**、门槛
  `VIDEO_MIN_VRAM_MB = 6144MB` 直接矛盾，已改成 6GB 档 + 「实测峰值约 2GB，OOM 说明有别的东西占着显存」。
- `app/engine/image/sd15.py`：同样的「RTX 3060 **12GB** 起」→ 「按 6GB 显存档位设计：fp16 + 卸载 +
  tiling，真机实测峰值 2051MB」。
- 网页端/桌面端的视频提示与报错在上一节已统一到 6GB；本轮把引擎内部的残留也清掉，
  **两端与引擎内部现在只有一种说法**。

### 1️⃣5️⃣ 规则 5 落地：一个模块一套外观，四套皮肤两端一致

| 模块 | 参照对象 | 网页端实现 | 桌面端实现 |
| --- | --- | --- | --- |
| AI 智能体 | ChatGPT | `html[data-mode="agent"]`：青绿强调 `#0f9d8f`、气泡圆角 16px、用户气泡带强调色底、发送键胶囊 | `theme.MODE_ACCENTS["agent"]`（同一色值） |
| 文生图 / 图生图 | Midjourney | **不覆盖**强调色，沿用 `:root` 近白 `#d8dade` + 四栏网格 | `MODE_ACCENTS["t2i"/"i2i"] = None`（同一语义） |
| 文生视频 / 图生视频 | PIKA | `#e8615c` 影院红、feed 顶部一道径向光晕、输入框上方一条**胶片刻度**、视频块描边 | 同色值 + `composerBar` 顶边 2px 强调色描边 |
| AI 音乐 | SUNO | `#b15cf6` 紫、输入框上方一条**波形示意**、描述框拉高成歌词卡（96px、16px 圆角） | 同色值 |

单一来源 + 对拍测试：`app/web/static/style.css` 与 `app/ui/theme.py::MODE_ACCENTS` 的色值必须逐字相同，
`tests/test_mode_theme.py`（7 条）把两端对拍钉死，改了任意一端、另一端不跟着改，测试立刻红；
同时钉住「图生图/文生图必须保持 MJ 中性色」、`build_stylesheet(dark, mode)` 的未知模式回落、
以及 `index.html` 缓存号必须已 bump（本轮 `?v=20261108e`，防止浏览器拿旧 CSS）。

桌面端切模式时 `MainWindow._on_mode_changed` 重算样式表，所以强调色、进度条、主按钮会跟着模式换；
AI 智能体模式下依旧隐藏右侧参数栏（智能体自带输入区）。

### 1️⃣6️⃣ 测试矩阵（本轮新增 26 条，全量 209 passed / 10 skipped）

| 解释器 | 范围 | 结果 |
| --- | --- | --- |
| `.venv` | `tests` 全量 | **209 passed / 10 skipped**，exit 0（34.7s） |
| `.venv` | `tests\test_memprobe.py`（新增） | 14 passed：门槛判定、读不到数值不假拦截、env 乱填回落、文案含实测值/门槛/下一步 |
| `.venv` | `tests\test_mode_theme.py`（新增） | 7 passed：四套皮肤存在、PIKA 胶片条 / SUNO 波形条 / ChatGPT 气泡、两端色值逐字对拍、缓存号已 bump |
| `.venv` | `tests\test_desktop_ui.py`（补一条） | 新增：桌面端切模式后样式表真的重算（music/t2v 出现对应强调色、切回 i2i 三种自定义色全部消失） |
| `.venv` | `tests\test_console_encoding.py`（新增） | 4 passed：兜底参数必须是 errors=replace、幂等、两端入口都调用、六个 bat 都有 chcp/PYTHONIOENCODING |
| `.venv-video` / `.venv-image` | 真机闸门验证 | 两个引擎都在 1.1GB 空闲内存下**给出中文可操作错误**，而不是让进程静默消失 |
| `node --check` | `app/web/static/app.js` | exit 0 |

**六能力状态不变，仍是 4/6**（文生图、图生图、文生视频、AI 智能体已真机通过；图转视频与 AI 音乐
代码路径就绪、被资源闸门诚实拦住）。本轮的实质变化是：**资源不够时不再「进程凭空消失」，
而是明确缺多少、该怎么办**；剩余两项等用户释放资源后跑 `scripts\acceptance_chain.ps1` 即可闭环。

### 1️⃣7️⃣ 顺手修掉一个「一启动就崩」的真 bug：GBK 控制台打不出中文和 emoji

本轮真机连撞两次：`scripts\verify_env.py` 在 PowerShell 里 `print("🔍 检查 PyTorch...")` 直接抛
`UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f50d'` 崩掉——**环境验证脚本自己先崩，
用户看不到任何结论**；README 追加脚本也在同一处崩过。根因：Windows 中文控制台默认代码页 936（GBK），
Python 的 stdout 跟着它编码，中文 emoji 打不出来就抛异常。

修法分三层，两端和所有用户入口都覆盖：

| 层 | 做了什么 |
| --- | --- |
| `app/core/console.py`（新建） | `ensure_utf8_console()`：把 stdout/stderr 切到 UTF-8 且 `errors=replace`；幂等，流被重定向/关闭/没有 `reconfigure` 时安静跳过——**兜底本身绝不能再成为崩溃源** |
| 两端入口 | `app/bootstrap.py`（桌面端）与 `app/web/server.py`（网页端）的 `main()` 第一件事就是调用它 |
| 手跑脚本 | `verify_env.py`、`check_hardware.py`、`acceptance_image.py`、`acceptance_video.py`、四个 `download_*_weights.py` 内联同样的兜底（不依赖 `app` 包，脚本本身坏了也要能打印原因） |
| 双击入口 | `acceptance_image/video/music.bat`、`run_web.bat`、`run_agent_llm.bat`、`release_local_engine.bat` 统一加 `chcp 65001` 与 `set "PYTHONIOENCODING=utf-8"` |

真机验证（故意**不设** `PYTHONIOENCODING`、在 GBK 控制台里跑）：`verify_env.py` 正常打出 🔍 与中文并
exit 0；`check_hardware.py` 正常输出「硬件检测：显卡 RTX 3060（12288MB） · 内存 63.8GB · 评级【达标】」；
`download_image_weights.py -VerifyOnly` 正常输出中文校验报告。引擎 worker 子进程本来就有
`process_worker` 里的 stdio 重配置，与这层互不影响。新增 `tests/test_console_encoding.py`（4 条）钉住
上述四条要求，改回去就会红。


### 1️⃣8️⃣ 交付前补验手册（图转视频 + AI 音乐，按顺序做，别跳步）

前提只有一个：**把显存和物理内存同时腾出来**。当前实测空闲显存 1322MB、空闲内存 1.2GB，
两端预检会诚实拦下并 exit 2（不会硬 OOM、不会伪造产物）。

1. 双击 `scripts\release_local_engine.bat`。它只结束 `%LOCALAPPDATA%\Strata\components\` 下
   **本项目自己的**推理进程（`strata.exe` / `strata-vision.exe`），明确不碰控制端、网页控制台、python，
   也绝不杀无关第三方程序。
   ⚠️ 真实副作用：这个进程同时是本机正在使用的本地大模型服务，**释放它 = 当前依赖它的本地对话会中断**，
   脚本自己也提示释放后约 40 分钟内不要再打开 Strata 本地聊天。所以补验要挑没人用这台机器的时候做。
   验完智能体功能前重新双击 `scripts\run_agent_llm.bat` 把大模型拉回来（约 40 秒）。
2. 双击 `scripts\acceptance_chain.ps1`（或分开跑）。它会按 **图片 → 视频 → 音乐** 顺序自动等窗口，
   每 180s 读一次 `nvidia-smi` 与 `Win32_OperatingSystem.FreePhysicalMemory`，**显存和内存两个条件同时满足才启动**，
   日志落 `logs\acceptance_<步骤>_r<轮>.log`。参数：`-MaxRounds`（默认 48；别写成 `-Rounds`）。
3. 单项验收：
   - 图转视频：`scripts\acceptance_video.bat`（会自动拿图像验收目录里最新的 PNG 当首帧，目录名跟着图像验收脚本走）。
     窗口条件：空闲显存 ≥4000MB **且空闲内存 ≥16000MB**。首帧条件：VACE 加载要 16-18GB 物理内存。
   - AI 音乐：`scripts\acceptance_music.bat`。窗口条件：空闲显存 ≥12300MB **且空闲内存 ≥36GB**
     （比引擎自身的 6000MB/14336MB 门槛更保守，图的是「一次跑成」而不是中途被杀）。
4. 判定只看产物：`output\` 下 mp4 ≥50KB、wav 48kHz 且时长合理，才算通过；脚本 exit 0 才算数。
   产物 metadata 里会记录 `free_ram_mb_at_load` 与 `free_vram_mb_at_load`，事后台账能还原当时的资源。

**为什么不能不释放就验**：这不是保守，是 2026-11-08 深夜的实证——VACE 加载到 checkpoint 1/2 时
系统只剩 2.5GB 物理内存，进程被系统直接杀掉，日志里连 traceback 都没有。现在有了内存闸门，
同样情况下会明确告诉你缺多少、该怎么办，而不是让进程凭空消失。


