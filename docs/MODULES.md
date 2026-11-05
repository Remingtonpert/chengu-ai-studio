# 模块清单与接口文档（docs/MODULES.md）

> 对应 README「模块化」约束：桌面端（PySide6）与 Web 控制台**共用同一套服务层与引擎 worker**，
> 本文档列出每个可独立复用模块的路径、职责与公开接口。以当前代码为准（2026-11-05）。

## 0. 分层总览

```
表现层     app/ui（Qt 桌面）           app/web（纯标准库 asyncio HTTP）
              \                         /
服务层            app/tasks（TaskService / EngineManager / worker 协议）
                    app/engine_registration（注册装配，无 Qt 依赖）
                        app/core（config / paths / logging / hardware）
引擎层    .venv-image worker（FLUX 图像）   .venv-music worker（ACE-Step 1.5）
              仅通过 app/tasks/process_worker 的 JSON-lines IPC 与主进程通信
```

主进程（.venv，Qt 或 Web）永远不加载 torch/diffusers/acestep；引擎只存在于独立 venv 的子进程里，
崩溃/显存泄漏不会波及 UI 或 HTTP 服务。

## 1. app/core —— 应用基础设施（零第三方依赖）

### `app/core/paths.py` — `AppPaths`
| 接口 | 说明 |
|---|---|
| `AppPaths.from_environment()` | 读取 `CHENGUAI_APP_HOME` / `CHENGUAI_DATA_ROOT` 构造路径集 |
| 属性 `config_file / database_file / log_dir / models_dir / output_dir` | 配置在 APP_HOME；任务库在 APP_HOME（默认 `%LOCALAPPDATA%\ChenguAIStudio\studio.sqlite3`）；权重/输出在 DATA_ROOT（默认项目根 `data\`，Windows 下与 `models\`、`output\` 同源可用） |
| `prepare_app_home()` | 创建应用数据目录 |

### `app/core/config.py` — `ConfigStore`
| 接口 | 说明 |
|---|---|
| `load() -> dict` / `save(config)` | JSON 读写（APP_HOME\config.json），带类型与范围校验 |
| 关键键 | `models_dir`、`output_dir`、`engines_enabled`(默认 false，真实环境已开启)、`image_venv_python`、`music_venv_python`、`music_model_dir`、`music_repo_dir`、`music_lm_model`、`music_max_tasks_per_worker`(1-50)、`web_host`、`web_port`、`llm_endpoint`、`llm_model` |

### `app/core/hardware.py`
`get_gpu_info()`（nvidia-smi 解析）、`get_disk_free_gib(path)`；只读本机，不上报任何外部。

## 2. app/tasks —— 任务与引擎运行时（两种模式共用，无 Qt）

### `app/tasks/models.py`
- `TaskStatus`：`queued / preparing / running / completed / failed / cancelled`（合法迁移见 `service._TRANSITIONS`）。
- `TaskRequest(task_type, engine_id, prompt, model_id=None, input_files=(), parameters={})`；`task_type ∈ {image, video, music}`。
- `TaskResult(output_files, metadata, test_only)`；`TaskRecord`（持久化读回形态，含 `retry_of`）。

### `app/tasks/service.py` — `TaskService`（SQLite FIFO，单执行者）
| 接口 | 说明 |
|---|---|
| `submit_task(request) -> TaskRecord` | 校验（类型/引擎已注册/prompt 非空/JSON 可序列化）后落库 `queued` |
| `await start()` | **跨进程执行者锁**（`<db>.worker.lock`，OS 崩溃自动释放）：抢到锁才运行 worker 循环与中断恢复；抢不到则本进程「只提交/查看」 |
| `_claim_task(task_id)`（内部） | `UPDATE … WHERE status='queued'` 原子领取，两进程不可能重复执行同一任务 |
| `cancel_task(task_id)` / `retry_task(task_id)` | 排队中直接取消（跨进程安全：状态条件更新）；运行中**仅能取消本进程（持锁执行者）正在执行的任务**——另一进程运行中的任务无法从旁观进程取消，这是当前版本诚实的边界；失败/取消可重试（`retry_of` 链） |
| `get_task / list_tasks(limit≤1000)` | 只读查询 |
| `await wait_until_idle()` / `await shutdown()` | 测试/退出辅助 |
| worker 循环 | 5 秒超时轮询 → 另一进程（桌面或另一控制台）排入的任务最迟 5 秒被执行者领走 |

### `app/tasks/engine_manager.py`
- `EngineAdapter` Protocol：`engine_id / task_types / test_only / health_check() / validate_request() / generate() / shutdown()`。
- `EngineManager`：`register_factory(engine_id, factory)`、`has_engine`、`list_statuses()`（`stopped/starting/ready/failed`）、`start_engine / stop_engine / restart_engine / mark_failed / shutdown`（懒启动、失败隔离）。

### `app/tasks/process_adapter.py` + `app/tasks/process_worker.py` — 跨 venv IPC 协议
- 主进程 `ProcessEngineAdapter(engine_id, command=[python, "-m", "app.tasks.process_worker", "--adapter-factory", "module:callable"], task_types, cwd, request_timeout_seconds)`。
- 协议：stdin/stdout **每行一条 JSON**。请求：`{"id", "method": "health"|"validate_request"|"generate"|"shutdown", "payload": TaskRequest 形态或 null}`；响应：`{"id", "ok": true, ...}` 或 `{"id", "ok": false, "error"}`（另有超时保护：`engine IPC <method> 请求超时`）。
- worker 侧 `--adapter-factory` 指定 `module:callable`，由 worker 进程 import 并构造实现 `EngineAdapter` 的对象。

### 两个 worker 适配器（运行在引擎 venv 内；视频引擎已于 2026-11-05 下线）
| 模块 | venv | 参数白名单（parameters） |
|---|---|---|
| `app/tasks/flux_worker.py` | .venv-image | `width, height, steps, seed, negative_prompt, init_image_path`（img2img） |
| `app/tasks/music_worker.py` | .venv-music | `bpm(30-300), duration(10-600；12GB 档截到 480), instrumental, vocal_language, keyscale, timesignature, lyrics(≤4096), seed, steps`；硬门槛 12GB 显存；worker 每 4 任务自重启（防泄漏）；输出真实 48kHz wav |

### `app/engine_registration.py`（无 Qt，桌面/Web 同一入口）
- 常量 `IMAGE_ENGINE_ID="flux-schnell"`、`MUSIC_ENGINE_ID="ace-step-15"`（原 `VIDEO_ENGINE_ID="anima-image2video"` 已于 2026-11-05 随 Anima 引用整体移除）。
- `register_production_engines(engine_manager, config, paths, logger)`：仅 `engines_enabled=true` 注册；`image_venv_python`/`music_venv_python`/`video_venv_python` 配置值指向失效路径时告警并回退对应默认（保证 worker 永远落在项目 venv，不会漂到系统 Python）；`CHENGUAI_DISABLE_MODALITIES`（如 `"music"`）按硬件分级跳过模态；注册 image（flux）、video（`wan-t2v`/`wan-i2v`，需 `.venv-video` python 存在；权重缺失照常注册、health_check 诚实报错）、music（ace-step-15）三模态；向 worker 注入 `CHENGUAI_MODEL_CACHE_DIR / CHENGUAI_OUTPUT_DIR / CHENGUAI_{FLUX,MUSIC,VIDEO}_ENGINE_ID / CHENGUAI_{FLUX,MUSIC,VIDEO}_MODEL_CACHE_DIR / CHENGUAI_MUSIC_{CHECKPOINTS_DIR,REPO_DIR,LM_MODEL,MAX_TASKS_PER_WORKER}` 并默认 `HF_HUB_OFFLINE=1`。
- `default_image_venv_python / default_music_venv_python / default_music_repo_dir / default_music_model_dir`：便携包相对定位。

## 3. app/engine —— 引擎实现（只在引擎 venv 内 import）

- `app/engine/abstract.py`：`AIGenerationEngine`（`generate/memory_usage/requires_cuda`）+ `ImageGenerationEngine`（`generate_image(prompt,width,height,steps,seed,negative_prompt)`、`default_steps`）+ `VideoGenerationEngine`（`generate_video(prompt,image_path,num_frames,...)`）+ `AudioGenerationEngine`（`generate_audio(prompt,duration,sample_rate)`）。注：文件尾部 `NoOpImageEngine` 为历史占位死代码，现行链路不使用（真实引擎缺失时由「不注册引擎」拒绝任务，绝不占位）。
- `app/engine/prompt_utils.py`：`coerce_prompt_text(value, *, joiner=", ") -> str | None`——把 str/bytes/任意嵌套 list/tuple/标量混排规整为合法 `str`（tokenizers 只接受标量 str，嵌套 list 会抛 `TextEncodeInput` TypeError）。
- `app/engine/image/flux.py`：`FluxImageEngine(ImageGenerationEngine)`（diffusers 管线、本地快照离线加载、中文/英文原生）+ `FluxEngineError`；生成前解析创建输出目录。
- `app/engine/video/wan.py`：`WanVideoEngine(VideoGenerationEngine)`——`wan-t2v`（`WanPipeline`）/ `wan-i2v`（`WanVACEPipeline`，首帧=垫图条件，`prepare_video_and_mask` 首帧 mask=0、其余待生成）；本地 HF 快照离线加载（fp32 VAE + bf16 pipeline + `enable_model_cpu_offload` + UniPC `flow_shift=3.0`）；尺寸 16 倍数 + 像素预算收口、帧数 `4n+1`、固定 16fps、官方中文负面提示词；OOM 捕获给 `release_local_engine.bat` 指引，不伪造；输出 `wan_{t2v|i2v}_{时间戳}.mp4`（<1KB 判失败）。旧 Anima adapter 已于 2026-11-05 整体删除。
- `app/tasks/video_worker.py`：Wan 视频生产适配器（`.venv-video` 子进程）；参数白名单 `width/height/num_frames/steps/seed/negative_prompt`（i2v 另加 `init_image_path`；t2v 带垫图报错引导换引擎）；health_check 查 CUDA + 快照存在（缺失提示运行 `scripts\download_video_weights.py`）。

## 4. app/web —— 本地 Web 控制台（纯标准库 asyncio，127.0.0.1）

`create_services()` 与桌面端 bootstrap 相同的装配（ConfigStore/EngineManager/TaskService/register_production_engines）。路由：

| 方法/路径 | 语义 |
|---|---|
| `GET /` `/static/*` | 单页 UI（五模式 Tab）与静态资源 |
| `GET /api/health` | `{status: "ok"}` |
| `GET /api/engines` / `POST /api/engines/{id}/restart` | 引擎状态与重启 |
| `GET /api/hardware` | GPU/磁盘实况（前端显存预检） |
| `GET /api/tasks` | 任务列表（含产物 URL） |
| `POST /api/tasks` | 提交（body：task_type/engine_id/prompt/model_id?/input_files?/parameters）；parameters 含 `init_image_path` 时先做垫图预检（非空/盘符绝对路径/非目录/文件存在/后缀 png·jpg·jpeg·webp·bmp），失败返回 400 与中文提示，通过后归一化路径；前端 `refShapeError` 先行拦截目录尾巴与不支持后缀 |
| `GET /api/tasks/{id}` | 单任务详情 |
| `POST /api/tasks/{id}/cancel` 或 `/retry` | 取消 / 重试 |
| `GET /outputs/<file>` | 产物预览（output_dir 与 worker 同源：优先 `config["output_dir"]`） |

## 5. app/ui —— 桌面端 MJ 同款三栏创作界面（仅表现层，服务来自 bootstrap 注入）

2026-11-05 重构：旧的每模态一个 `WorkspacePage` 的十页表单式桌面已删除，`MainWindow._submit_generation` 入口不复存在。现为「左会话栏 · 中创作流 · 右参数面板」三栏，与 Web 控制台逐字对齐同一套提交语义（4 连发 seed 规则、垫图门槛、音乐显存门槛与能力确认、U/V 语义、帧数 4n+1 对齐等）。

- `app/bootstrap.py`：`create_application()` 装配与 Web 相同（ConfigStore / EngineManager / TaskService / `register_production_engines`）+ `start_task_service_loop()`（后台线程 asyncio 驱动 `task_service.start()`，返回 `(停止回调, 事件循环)`；`aboutToQuit` 优雅停止）。事件循环传给 `MainWindow`，桌面端因此能直接调用 `cancel_task` / `restart_engine` 等 async 接口（`asyncio.run_coroutine_threadsafe`）。
- `app/ui/main_window.py` — `MainWindow(config_store, logger, database_path, task_service, engine_manager, service_loop)`：三栏骨架。左栏 250px（新建会话、会话列表含缩略图/改名/删除、「系统功能」导航 7 项进入 `SystemPageShell`，`QStackedWidget` 返回创作页）；右栏 320px `ParamPanel`；中部 `CreationPage`。会话与参数桶持久化在同 APP_HOME 的 `desktop_sessions.json`。
- `app/ui/session_store.py` — `SessionStore`：会话/批次（batch）JSON 持久化（tempfile + `os.replace` 原子写），上限 120 会话，缩略图路径、每模式参数桶、`refDir` 记忆。
- `app/ui/pages/creation_page.py` — `CreationPage`：五模式 Tab（t2i/i2i/t2v/i2v/music）+ 顶栏引擎/健康 pill + 队列徽标；`imagine()` 唯一提交入口：图像模式 4 连发（seedLock 则 seed+i，否则随机互异）、`NEEDS_REF` 垫图门槛、`_ref_path_error` 垫图文件校验（composer 与高级参数白名单两处）、音乐四重门槛（引擎未注册 / 无 GPU / 总显存 <12288MiB / 空闲 <7000MiB）+ 能力边界确认弹窗；upscale（strength 0.45、面积上限 1,350,000px 超界等比缩、宽高对齐 8）与 variation（4 张、strength 0.7）从瓦片/lightbox 发起；retry（失败任务参数快照重投，模式回退）、cancel（经服务事件循环）、引擎重启、2.5s 轮询刷新流与徽标、断点队列位置提示。7 个系统页（仪表盘/队列/历史/模型/引擎/设置/诊断）保留在同一窗口内。
- `app/ui/widgets/`：`param_panel.py`（参数面板，按模式分桶、引擎选择/状态、音乐 ready 文案）、`composer.py`（提示词框：Enter 提交、粘贴/拖放存 `refs\pasted_*.png`、垫图 chip 与本机路径编辑）、`feed.py`（批次流渲染、自动滚底、骨架瓦片）、`tiles.py`（图片/视频/音乐/错误瓦片与工厂，读 `rec.request.*`）、`lightbox.py`（←/→/v/u/Esc，V/U 信号）、`skeleton.py`（加载扫掠动画）、`common.py`（格式化、打开文件/资源管理器、toast 中枢）。
- `app/ui/theme.py`：MJ 同款令牌（暗/亮双主题，`CHENGUAI_THEME=auto|dark|light`），`build_stylesheet(dark)` 全局 QSS；主题 4 秒轮询自动跟随系统。

## 6. 环境变量参考（worker/启动端约定）

| 变量 | 作用 |
|---|---|
| `CHENGUAI_APP_HOME` / `CHENGUAI_DATA_ROOT` | 路径根（便携包首启改写） |
| `CHENGUAI_MODEL_CACHE_DIR` / `CHENGUAI_OUTPUT_DIR` | worker 权重缓存 / 输出目录（factory 注入） |
| `CHENGUAI_FLUX_ENGINE_ID` / `CHENGUAI_VIDEO_ENGINE_ID` / `CHENGUAI_MUSIC_ENGINE_ID` | worker 侧引擎 id |
| `CHENGUAI_DISABLE_MODALITIES` | 硬件分级禁注册（`"music"` 形式；视频已无引擎可禁） |
| `CHENGUAI_MUSIC_*` | 音乐 checkpoints/repo/LM/自重启阈值 |
| `HF_HUB_OFFLINE=1` 等 | 强制离线加载本地权重（setdefault，可显式覆盖） |
| `ACESTEP_CHECKPOINTS_DIR / CONFIG / LM_MODEL / BACKEND=pt / OFFLOAD_TO_CPU=true / OFFLOAD_DIT_TO_CPU=true` | ACE-Step 1.5（12GB 档必须开 DiT offload，上游 #1337） |

## 7. 独立复用指南（第三方只想要「本地任务队列 + 引擎 worker」）

1. `TaskService(db_path, EngineManager())` + `register_production_engines(...)`（或自注册自己的 `EngineAdapter`/`--adapter-factory` worker）。
2. 一个事件循环里 `await task_service.start()`（可与其它 asyncio 服务共存；多进程共存自动降级为只提交）。
3. `submit_task(TaskRequest(...))` → 轮询 `get_task`；取消/重试语义见上表。
   引擎侧只需实现 `EngineAdapter` 协议并（可选）放进独立 venv 经 `process_worker` 拉起。
