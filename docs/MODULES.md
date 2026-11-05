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
引擎层    .venv-image worker（FLUX 图像 / Anima 视频）   .venv-music worker（ACE-Step 1.5）
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
| `cancel_task(task_id)` / `retry_task(task_id)` | 排队中直接取消；运行中取消当前进程的执行器；失败/取消可重试（`retry_of` 链） |
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

### 三个 worker 适配器（运行在引擎 venv 内）
| 模块 | venv | 参数白名单（parameters） |
|---|---|---|
| `app/tasks/flux_worker.py` | .venv-image | `width, height, steps, seed, negative_prompt, init_image_path`（img2img） |
| `app/tasks/video_worker.py` | .venv-image | `width, height, num_frames, steps, seed, negative_prompt, init_image_path`（留空自动文生图首帧） |
| `app/tasks/music_worker.py` | .venv-music | `bpm(30-300), duration(10-600；12GB 档截到 480), instrumental, vocal_language, keyscale, timesignature, lyrics(≤4096), seed, steps`；硬门槛 12GB 显存；worker 每 4 任务自重启（防泄漏）；输出真实 48kHz wav |

### `app/engine_registration.py`（无 Qt，桌面/Web 同一入口）
- 常量 `IMAGE_ENGINE_ID="flux-schnell"`、`VIDEO_ENGINE_ID="anima-image2video"`、`MUSIC_ENGINE_ID="ace-step-15"`。
- `register_production_engines(engine_manager, config, paths, logger)`：仅 `engines_enabled=true` 注册；`CHENGUAI_DISABLE_MODALITIES`（如 `"video,music"`）按硬件分级跳过模态；向 worker 注入 `CHENGUAI_MODEL_CACHE_DIR / CHENGUAI_OUTPUT_DIR / CHENGUAI_{FLUX,VIDEO,MUSIC}_ENGINE_ID / CHENGUAI_MUSIC_{CHECKPOINTS_DIR,REPO_DIR,LM_MODEL,MAX_TASKS_PER_WORKER}` 并默认 `HF_HUB_OFFLINE=1`。
- `default_image_venv_python / default_music_venv_python / default_music_repo_dir / default_music_model_dir`：便携包相对定位。

## 3. app/engine —— 引擎实现（只在引擎 venv 内 import）

- `app/engine/abstract.py`：`AIGenerationEngine`（`generate/memory_usage/requires_cuda`）+ `ImageGenerationEngine`（`generate_image(prompt,width,height,steps,seed,negative_prompt)`、`default_steps`）+ `VideoGenerationEngine`（`generate_video(prompt,image_path,num_frames,...)`）+ `AudioGenerationEngine`（`generate_audio(prompt,duration,sample_rate)`）。注：文件尾部 `NoOpImageEngine` 为历史占位死代码，现行链路不使用（真实引擎缺失时由「不注册引擎」拒绝任务，绝不占位）。
- `app/engine/prompt_utils.py`：`coerce_prompt_text(value, *, joiner=", ") -> str | None`——把 str/bytes/任意嵌套 list/tuple/标量混排规整为合法 `str`（tokenizers 只接受标量 str，嵌套 list 会抛 `TextEncodeInput` TypeError）。
- `app/engine/image/flux.py`：`FluxImageEngine(ImageGenerationEngine)`（diffusers 管线、本地快照离线加载、中文/英文原生）+ `FluxEngineError`；生成前解析创建输出目录。Anima 见 `app/engine/video/anima.py`。

## 4. app/web —— 本地 Web 控制台（纯标准库 asyncio，127.0.0.1）

`create_services()` 与桌面端 bootstrap 相同的装配（ConfigStore/EngineManager/TaskService/register_production_engines）。路由：

| 方法/路径 | 语义 |
|---|---|
| `GET /` `/static/*` | 单页 UI（五模式 Tab）与静态资源 |
| `GET /api/health` | `{status: "ok"}` |
| `GET /api/engines` / `POST /api/engines/{id}/restart` | 引擎状态与重启 |
| `GET /api/hardware` | GPU/磁盘实况（前端显存预检） |
| `GET /api/tasks` | 任务列表（含产物 URL） |
| `POST /api/tasks` | 提交（body：task_type/engine_id/prompt/model_id?/input_files?/parameters） |
| `GET /api/tasks/{id}` | 单任务详情 |
| `POST /api/tasks/{id}/cancel` 或 `/retry` | 取消 / 重试 |
| `GET /outputs/<file>` | 产物预览（output_dir 与 worker 同源：优先 `config["output_dir"]`） |

## 5. app/ui —— 桌面端（仅表现层，服务来自 bootstrap 注入）

- `app/bootstrap.py`：装配与 Web 相同 + `start_task_service_loop()`（后台线程 asyncio 驱动 `task_service.start()`；`aboutToQuit` 优雅停止）。
- `MainWindow._submit_generation(kind, prompt, parameters)`：三模态唯一提交入口（`kind ∈ image/video/music` → 对应 ENGINE_ID）。
- `WorkspacePage(kind, engine_ready, engine_label, on_generate)`：按模态渲染表单；按钮启用只取决于引擎注册；表单校验与 worker 白名单一致。

## 6. 环境变量参考（worker/启动端约定）

| 变量 | 作用 |
|---|---|
| `CHENGUAI_APP_HOME` / `CHENGUAI_DATA_ROOT` | 路径根（便携包首启改写） |
| `CHENGUAI_MODEL_CACHE_DIR` / `CHENGUAI_OUTPUT_DIR` | worker 权重缓存 / 输出目录（factory 注入） |
| `CHENGUAI_FLUX_ENGINE_ID` / `CHENGUAI_VIDEO_ENGINE_ID` / `CHENGUAI_MUSIC_ENGINE_ID` | worker 侧引擎 id |
| `CHENGUAI_DISABLE_MODALITIES` | 硬件分级禁注册（`"video,music"` 形式） |
| `CHENGUAI_MUSIC_*` | 音乐 checkpoints/repo/LM/自重启阈值 |
| `HF_HUB_OFFLINE=1` 等 | 强制离线加载本地权重（setdefault，可显式覆盖） |
| `ACESTEP_CHECKPOINTS_DIR / CONFIG / LM_MODEL / BACKEND=pt / OFFLOAD_TO_CPU=true / OFFLOAD_DIT_TO_CPU=true` | ACE-Step 1.5（12GB 档必须开 DiT offload，上游 #1337） |

## 7. 独立复用指南（第三方只想要「本地任务队列 + 引擎 worker」）

1. `TaskService(db_path, EngineManager())` + `register_production_engines(...)`（或自注册自己的 `EngineAdapter`/`--adapter-factory` worker）。
2. 一个事件循环里 `await task_service.start()`（可与其它 asyncio 服务共存；多进程共存自动降级为只提交）。
3. `submit_task(TaskRequest(...))` → 轮询 `get_task`；取消/重试语义见上表。
   引擎侧只需实现 `EngineAdapter` 协议并（可选）放进独立 venv 经 `process_worker` 拉起。
