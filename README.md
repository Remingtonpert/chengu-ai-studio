# 辰鹄 AI 创作中心（Chengu AI Studio）

> 本地化 AI 图片/视频/音频生成平台

## 📊 项目状态概览

| 阶段 | 状态 | 描述 |
|------|------|------|
| **Phase 0** | ✅ 已完成 | 引擎调研、许可证审计、硬件兼容性分析、架构提案、开发计划 |
| **Phase 1** | ✅ 已完成 | 桌面骨架与本地基础（PySide6 UI、SQLite、配置服务） |
| **Phase 2** | ✅ 已完成 | 任务系统核心（FIFO 队列、状态管理、引擎生命周期隔离） |
| **Phase 3** | 🚧 进行中 | 真实引擎已接入：FLUX.1-schnell 图像与 Anima 视频引擎已注册（`.venv-image` 独立 worker 子进程）；FLUX 权重（23 文件 / 33.73 GB）已在 `models\flux-schnell` 并 SHA-256 逐文件校验通过；**Web 控制台（Midjourney 风格三栏 + 五模式 Tab）**已上线；音乐模型选型调研已完成并已实装首选方案 ACE-Step 1.5（`.venv-music` 独立环境 + 独立 worker，MIT 权重 10.03 GB 已就位、四文件 safetensors 逐文件校验通过【权重就绪】）；真机验收进行中 |

**重要提示**：图片与视频引擎已**接入**本地 worker（FLUX.1-schnell、Anima），FLUX.1-schnell 权重已就位并通过 SHA-256 逐文件校验；**桌面端已换成与网页端几乎一模一样的 MJ 深色三栏 + 五模式 Tab 创作界面**（文生图/图生图/文生视频/图生视频/AI 音乐五个 Tab 与网页端共用同一 `TaskService` 提交路径，见下文 2026-11-05「桌面端 MJ 同款创作界面」一节），能否提交只取决于引擎是否注册（权重与依赖在注册时校验），不提供任何模拟生成。AI 音乐引擎 ACE-Step 1.5 已注册（最低 12GB 显存，全本地离线推理，提交前前端自动做显存预检与能力边界确认），真机端到端验收完成前勿视为正式产出。程序不会产生占位图或模拟输出；验收通过前请勿将任何结果视为正式产出。桌面界面跟随 Windows 系统深/浅色主题自动切换。

**已知运行约束**：本机另有 LLM 推理引擎常驻（约占 11.5 GB 显存 + 40 GB 内存），与 FLUX/Anima 互斥；生成图片/视频前先双击 `scripts\release_local_engine.bat` 释放显存（只结束 components 目录下的引擎进程，不影响控制台）。Anima 视频权重（`moovenio/Anima_2B_model` + `dc-anime-tiny` VAE + `gemma-3-4b-it` 文本编码器，社区类许可，商用前需审查）**尚未下载**，首次生成视频会联网自动下载（十几分钟量级）。

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
│   ├── engine_registration.py   # 真实引擎注册（FLUX 图像 / Anima 视频 worker）
│   ├── engine/prompt_utils.py   # 提示词类型规整（FLUX 中英文输入防护，零重依赖）
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
│   │   ├── image/              # 图片引擎 adapter
│   │   │   ├── __init__.py
│   │   │   └── flux.py         # FLUX.1-schnell adapter
│   │   └── video/              # 视频引擎 adapter
│   │       ├── __init__.py
│   │       └── anima.py        # Anima 图生视频 adapter
│   ├── models/                  # 模型管理（待 Phase 5 实现）
│   │   ├── __init__.py
│   │   ├── manager.py          # 模型清单、校验与下载确认
│   │   └── registry.py         # 模型注册表
│   ├── tasks/                   # 任务系统核心
│   │   ├── __init__.py
│   │   ├── engine_manager.py   # 引擎生命周期管理（懒启动、健康检查）
│   │   ├── flux_worker.py      # FLUX 图像 worker（生产适配器，运行于 .venv-image 子进程）
│   │   ├── video_worker.py     # Anima 图生视频 worker（生产适配器，运行于 .venv-image 子进程）
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
│   │   │   ├── creation_page.py   # 创作页：五模式 Tab + 会话流 + 想象/V·U/重试/取消/lightbox
│   │   │   ├── engine_status_page.py
│   │   │   ├── settings_page.py
│   │   │   └── task_list_page.py
│   │   └── widgets/            # MJ 风格组件：参数面板/composer/feed/产物卡/骨架/lightbox/toast
│   └── utils/                   # 工具函数
│       └── config.py
├── tests/                       # 测试目录
│   ├── test_flux_image.py
│   ├── test_flux_worker_adapter.py
│   ├── test_phase1_foundation.py
│   ├── test_task_service.py
│   └── test_cross_process_safety.py  # 执行者锁/原子领取/5秒轮询接管专项测试
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
│   ├── acceptance_flux.bat/.ps1 # FLUX 真机验收（端到端出图 + 中文用例 + 离线校验）
│   ├── acceptance_music.bat/.ps1 # ACE-Step 端到端验收（中文人声 48kHz wav + 时长截断实测）
│   ├── acceptance_chain.ps1     # FLUX→音乐链式自动验收（资源窗口打开后自动跑，日志落 logs\）
│   ├── release_local_engine.bat/.ps1  # 释放本机 LLM 引擎显存（生成前必跑）
│   ├── setup_music_venv.ps1     # 音乐引擎环境一键搭建（.venv-music，幂等）
│   ├── download_music_weights.py # ACE-Step 1.5 权重下载（断点续传）
│   ├── finalize_music_weights.py # 权重下载监控 + 同步/校验自动收尾
│   ├── probe_flux_tokenizer.py  # FLUX tokenizer 形状/类型离线复现诊断
│   ├── probe_coerce.py          # 提示词规整（coerce_prompt_text）单元断言
│   ├── probe_task_db.py         # 任务库只读查询（排查历史失败任务）
│   ├── probe_flux_encode.py     # FLUX encode_prompt CPU 直测（可选，重内存）
│   ├── smoke_desktop_generation.py # 桌面端 MJ 同款创作界面冒烟（离屏 Qt，不启动 GPU worker）
│   ├── shot_desktop_ui.py       # 离屏验收截图：深色/浅色/系统页三张（.tmp_shots\）
│   ├── check_hardware.py        # 启动前置硬件检测（显存/内存分级，只读本机）
│   ├── fix_portable_paths.py    # 便携包首启路径自适应（纯本地文本替换）
│   ├── build_portable.ps1       # 一键便携打包脚本
│   └── portable/                # 打包模板：启动辰鹄.bat / 启动桌面端.bat / 打包与迁移说明.md
├── data/                        # 默认数据根：配置、会话数据库、日志、权重缓存、输出（可被环境变量覆盖）
├── .venv/                       # PySide6 桌面程序虚拟环境（Python 3.11）
├── .venv-image/                 # 图片/视频引擎专用虚拟环境（torch cu130 + diffusers 已装）
├── requirements-dev.txt         # 开发依赖（含 pytest）
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
| `CHENGUAI_DISABLE_MODALITIES=image,video,music` | 逗号分隔的禁用模态：对应引擎直接不注册（启动硬件检测分级自动注入；手工设置可用于调试） |
| `CHENGUAI_THEME=auto\|light\|dark` | 界面主题覆盖；默认 `auto` 跟随 Windows 系统深浅色，运行中切换系统主题即时生效 |

---

## 🌐 Web 控制台（Midjourney 风格 · 五模式 Tab）

浏览器版创作台，与桌面程序共用同一服务层与任务队列，地址 <http://127.0.0.1:8765>（仅本机）。

### 启动

```powershell
# 项目根目录执行（可设 HF_HUB_OFFLINE=1 强制离线）
.\.venv\Scripts\python.exe -m app.web.server
```

日志见 `logs\manual_console\console.out.log / console.err.log`。`static\` 目录实时读取，改前端**无需重启**。

### 界面结构（三栏）

- **左栏**：会话列表（新建/切换/双击标题或 ✎ 重命名/删除仅删本地分组）；
- **中栏**：对话流 + 底部 Imagine 输入（Enter 提交、Shift+Enter 换行、拖入/粘贴图片挂参考图）；
- **右栏**：参数面板（引擎、尺寸、强度、时长、steps、seed、负面词、风格、高级 JSON）。

### 顶部五模式 Tab（纯前端路由，后端接口不动）

| Tab | 提交 | 专属参数 | 后端行为 |
|-----|------|----------|----------|
| 文生图 | 4 张/次（`task_type=image`） | 尺寸/steps/seed | FLUX.1-schnell，4 步 |
| 图生图 | 4 张/次 | 上述 + **重绘强度** + **垫图上传区** | FLUX img2img（需本机绝对路径） |
| 文生视频 | 单任务（`task_type=video`） | 视频尺寸/**时长（5–161 帧）**/steps | FLUX 出首帧 → Anima 渲染 |
| 图生视频 | 单任务 | 同上 + **首帧图上传区** | Anima 图生视频 |
| AI音乐 | 单任务（`task_type=music`），提交前前端预检显存并弹能力边界确认 | BPM/目标时长（30–480s）/曲风 | **ACE-Step 1.5**（`.venv-music` 独立 worker），48kHz wav |

要点：
- **一次想象 = 4 个任务**；图片瓦片带 **V**（变体，strength 0.7×4）与 **U**（放大，1.5× + strength 0.45）；
- **会话全局共用**：同一会话可混排图片网格、视频卡片（`<video>` 播放 + 首帧缩略 + ⬇下载）、未来音频播放条；
- Tab 徽标 = 该模态活跃任务数。注意：各模式**前端提交与展示相互独立**，底层 TaskService 是**单一 FIFO 队列（串行执行）**，切 Tab 不会并发抢占；
- 视频硬约束来自 Anima：导出固定 **24 fps**，帧数必须为 4n+1（最长约 6.7 秒），引擎不暴露运动幅度/帧率参数（面板中为带说明的禁用占位）；尺寸 16 的倍数，建议 ≤512×384；
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

## 🔧 FLUX 稳定性修复（2026-11-05）：中英文提示词 + 输出目录

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

## 🎨 桌面端 MJ 同款创作界面（2026-11-05）

桌面端从「十个侧栏页 + 生成表单」重构为与 Web 控制台**一模一样的 Midjourney 风格三栏创作界面**；`app/tasks`、`app/engine`、`app/web` 与共享服务层一行未改，`app/ui/` 之外只动了 `app/bootstrap.py` 两处。

### 布局与视觉

| 区域 | 内容 |
| --- | --- |
| 左栏（250px） | 品牌区 + 引擎状态副标题、`＋ 新建会话`、会话列表（缩略图 / 标题 / ✎ 重命名 / 🗑 删除，删除仅删本机会话分组，服务器任务与产物不受影响）、`系统功能` 七个入口 |
| 中栏 | 顶栏（会话标题 + 引擎药丸 + 任务服务健康药丸 + `⚙`）、**五模式 Tab**（文生图 / 图生图 / 文生视频 / 图生视频 / AI 音乐，Tab 右上角蓝色角标 = 该模态活动任务数）、会话流（批次气泡 + kind/mode/时间标签 + 产物网格）、composer（垫图区 + 多行提示词 + `想象 Enter` + 参数摘要行） |
| 右栏（320px） | 引擎选择与「重启」、按模式显隐的参数分区：尺寸 chips + 滑杆 + 数字框、steps（图 ≤12 / 视频 ≤50）、seed 锁定与随机、负向提示词、风格 / 曲风 chips（改写提示词）、图生图强度、视频尺寸与帧数、音乐 BPM 与时长、高级参数 JSON 白名单 |

配色直接沿用网页端 `style.css` 的 token（`--bg:#0a0b0d`、`--bg-soft:#101114`、`--bg-card:#16181c`、`--line:#23262c`、`--text:#e8eaed`、`--accent:#d8dade`、`--blue:#7d9bff`、`--red:#f07171`、`--green:#6fd08c`、`--radius:12px`），并继续跟随系统深/浅色（`CHENGUAI_THEME=light|dark` 可强制，4 秒轮询热切换）。

### 与网页端逐字对齐的行为（`app/ui/pages/creation_page.py`）

- **提交**：一次「想象」排队 4 张；`seedLock` 时用 `seed+i`，否则每张随机 seed；提交后立即清空 composer 并滚到底部。
- **门槛与文案**（与 `app.js` 完全一致）：空提示词「先写提示词」；无引擎「没有已注册的引擎，请先检查 engines_enabled 与引擎环境」；`i2i`/`i2v` 缺垫图各自专属文案；垫图路径必须是盘符开头的绝对路径，例：`D:\ChenguAI\uploads\photo.png`。
- **视频**：`num_frames` 按 `4n+1` 吸附（24fps，5–161），参数快照含 `width/height/steps/num_frames/negative_prompt/seed`，垫图存在时写 `init_image_path`（优先于高级参数）。
- **音乐**：四道门槛顺序与文案逐字——引擎未注册 / 检测不到 NVIDIA 显卡 / 显存总量 < 12288 MB / 空闲显存 < 7000 MB（提示双击 `scripts\release_local_engine.bat`）；通过后弹四行能力确认（4–10 分钟、12GB 约 8 分钟截断、不支持多轨、BPM·时长·串行队列），成功 toast 说明首次加载约 10GB 权重与 48kHz wav。
- **V / U / 重试 / 取消**：`V` 强度 0.7 排 4 张变体；`U` 按 1.5×（受 1,350,000 像素安全上限回缩）强度 0.45 重建；失败任务「重试」走 `TaskService.retry_task`，命中原批次则并入，否则按参数回退模式建新批次；取消与引擎重启通过 `asyncio.run_coroutine_threadsafe` 回投到服务事件循环，结果用 toast 回执（跨线程经类信号，不用 `QTimer.singleShot`）。
- **产物展示**：图片 4 格网格（单任务/视频/U 放大多为单列）、悬停淡入 `V 变化`/`U 放大`、`seed x · W×H` 角注、lightbox 大图（←/→、V/U、复制提示词、打开所在文件夹）、视频卡片（首帧缩略图 + 打开视频/文件夹）、音乐内嵌播放条（进度、时长、失败自动降级系统播放器）、加载骨架（取消按钮 + 排队位次 + 管线说明）、失败卡片（重试 / 「已重试 →」）、toast 三态。
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

## 📦 全模块本地离线便携打包（第三段 · 已实装；真实打包待音乐权重校验通过后执行）

目标：**文生图/图生图/文生视频/图生视频/AI音乐全部推理本地离线运行**（仅首次下载权重联网），
一键打包出完整目录包，复制到其他 Windows 电脑直接双击启动；不引入任何在线校验、远程上报、云端依赖。

### 一键打包：`scripts\build_portable.ps1`

```powershell
# 项目根目录执行；整包含权重约 60GB+，目标盘预留 ≥80GB
pwsh -File scripts\build_portable.ps1                 # 完整打包 → dist\ChenguAIStudio_Portable\
pwsh -File scripts\build_portable.ps1 -SkipModels     # 调试：跳过权重复制
pwsh -File scripts\build_portable.ps1 -SkipVenvs      # 调试：跳过虚拟环境复制
pwsh -File scripts\build_portable.ps1 -OutDir E:\包   # 指定输出目录
```

包内容：应用代码 + **三个虚拟环境**（`.venv`/`.venv-image`/`.venv-music`）+ **包内自带 CPython 3.11 运行时**
（目标机无需装 Python）+ 已就位的权重（FLUX、ACE-Step；Anima 视频权重未下载则该模块自动不注册）+
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

**执行状态**：脚本与文档已开发并通过编译/干跑验证（打包实测 `-SkipModels` 待做）；**真实完整打包安排在
ACE-Step 权重校验通过（【权重就绪】）之后执行**，保证音乐权重随包且不再需要下载；Anima 视频权重下载
需单独批准后补进包。