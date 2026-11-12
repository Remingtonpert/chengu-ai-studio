# 模块清单与接口文档（docs/MODULES.md）
> **2026-11-10 更新（读前必看）**：文生图引擎现职为 **Sana-1.6B 多语言版**（引擎 id `sana-multiling`，`app/engine/image/sana.py` + 适配器 `app/tasks/sana_worker.py`，跑在 `.venv-image`），图生图由 **Wan2.1-VACE-1.3B**（引擎 id `wan-i2i`）承担，复用 `app/tasks/video_worker.py` 与 `.venv-video`。历史上的 FLUX.1-schnell（`app/tasks/flux_worker.py`、`app/engine/image/flux.py` 已删除）与 SD1.5 精调 RealVis（`sd15.py` / `sd_worker.py` 代码与权重仍在盘上，但**不注册、界面上看不到**）**都不是现役能力**。许可与选型结论以 `LICENSES.md`、`docs\LICENSE_AUDIT.md` 文末增补、`docs\ENGINE_RESEARCH.md` 文末「2026-11-10 文生图再次选型」为准。三条铁律：**可商用 / 中英文提示词 / 显存峰值 ≤6GB**。

> 对应 README「模块化」约束：桌面端（PySide6）与 Web 控制台**共用同一套服务层与引擎 worker**，
> 本文档列出每个可独立复用模块的路径、职责与公开接口。以当前代码为准（2026-11-10）。

## 0. 分层总览

```
表现层     app/ui（Qt 桌面）           app/web（纯标准库 asyncio HTTP）
              \                         /
服务层            app/tasks（TaskService / EngineManager / worker 协议）
                    app/engine_registration（注册装配，无 Qt 依赖）
                        app/core（config / paths / logging / hardware）
引擎层    .venv-image worker（Sana 文生图）  .venv-video worker（Wan2.1 视频 + 图生图）  .venv-music worker（ACE-Step 1.5）
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
| 关键键（`defaults` 实际写入） | `models_dir`、`output_dir`、`auto_save_dir`、`engines_enabled`(默认 false，真实环境已开启)、`video_venv_python`、`video_model_dir`、`web_host`、`web_port`、`llm_endpoint`、`llm_model`、`llm_autostart`、`llm_model_path`、`llm_timeout_seconds`、`upload_dir`、`ui_language` |
| 注册时读取、但**不在 `defaults` 里**的键 | `image_venv_python`、`image_model_dir`（图像引擎环境与权重目录）、`music_venv_python`、`music_model_dir`、`music_repo_dir`、`music_lm_model`、`music_max_tasks_per_worker`。留空即用 `default_*()` 的项目默认值；注意 `_validate()` 只遍历 `defaults`，这些键**手工写进 config.json 会在下次 `save()` 时被静默删掉**（`video_*` 两个键当初就是为此补进 `defaults` 的，图像/音乐这几个还没补，改环境目前只能靠默认路径或环境变量） |

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

### 生产 worker 适配器（运行在引擎 venv 内）
| 模块 | venv | 参数白名单（parameters） |
|---|---|---|
| `app/tasks/sana_worker.py` | .venv-image | `width, height, steps(引擎按 10-40 夹取，默认 20), seed, negative_prompt`；**明确拒绝** `init_image_path / strength / hires / image_path`——写了就直接报错而不是静默丢掉，报错文案把人指到「图生图」板块（引擎 `wan-i2i`）。CFG 固定 4.5，不开放成用户参数。health_check 查 CUDA + fp16 三件快照是否完整，缺失就点名 `scripts\download_sana_weights.py`，绝不自动下载 |
| `app/tasks/video_worker.py` | .venv-video | 文生视频 `wan-t2v` / 图生视频 `wan-i2v`；**图生图 `wan-i2i` 也走这个适配器**（factory 注入 `CHENGUAI_VIDEO_ENGINE_ID=wan-i2i`、`CHENGUAI_VIDEO_MODEL_CACHE_DIR=models\wan2.1`）；白名单 `width/height/num_frames/steps/seed/negative_prompt`（i2v 与 i2i 另加 `init_image_path`） |
| `app/tasks/music_worker.py` | .venv-music | `bpm(30-300), duration(10-600；12GB 档截到 480), instrumental, vocal_language, keyscale, timesignature, lyrics(≤4096), seed, steps`；硬门槛 12GB 显存；worker 每 4 任务自重启（防泄漏）；输出真实 48kHz wav |
| `app/tasks/sd_worker.py` | （**未注册**）.venv-image | SD1.5 + RealVis 的历史适配器：代码与权重仍在盘上，注册表里没有它，界面上看不到；要自行验证得手工 `register_factory`。保留理由是它是历史上唯一实测过峰值的出图路径（4359MB） |

### `app/engine_registration.py`（无 Qt，桌面/Web 同一入口）
- 常量 `IMAGE_T2I_ENGINE_ID="sana-multiling"`、`IMAGE_I2I_ENGINE_ID="wan-i2i"`、`VIDEO_T2V_ENGINE_ID="wan-t2v"`、`VIDEO_I2V_ENGINE_ID="wan-i2v"`、`MUSIC_ENGINE_ID="ace-step-15"`；`GENERATION_REQUEST_TIMEOUT_SECONDS=4200.0`（Sana 放大档与视频都要几十分钟量级，旧的短超时会在快出结果时判超时）。历史上还有过 `IMAGE_ENGINE_ID="flux-schnell"` 与 `VIDEO_ENGINE_ID="anima-image2video"`，分别于 2026-11-08、2026-11-05 随引擎整体移除。
- `register_production_engines(engine_manager, config, paths, logger)`：仅 `engines_enabled=true` 注册；`image_venv_python` / `music_venv_python` / `video_venv_python` 配置值指向失效路径时告警并回退对应默认（保证 worker 永远落在项目 venv，不会漂到系统 Python）；`image_model_dir` 留空即用 `default_image_model_dir()`（= 项目根 `models/sana-multiling`）。**注意一处代码待补**：`image_model_dir` 目前只解析进日志，Sana worker 实际按 `CHENGUAI_SANA_MODEL_CACHE_DIR` 定位权重，注册段还没注入它，没注入时退回 `<models_dir>/<engine_id>`；把权重放在非默认位置时，代码侧需要补上这个注入。
- `CHENGUAI_DISABLE_MODALITIES`（如 `"music"`）按硬件分级跳过模态；**图片引擎独立于 `video` 开关**：`image` 被禁时不注册 `sana-multiling` / `wan-i2i`，禁 `video` 不影响图片。注册 image（`sana-multiling` 经 `app.tasks.sana_worker:create_adapter`，task_types `{"image"}`；`wan-i2i` 经 `app.tasks.video_worker:create_adapter`）、video（`wan-t2v`/`wan-i2v`，需 `.venv-video` python 存在）、music（ace-step-15）；权重缺失照常注册、health_check 诚实报错。
- 向 worker 注入 `CHENGUAI_MODEL_CACHE_DIR / CHENGUAI_OUTPUT_DIR / CHENGUAI_SANA_{ENGINE_ID,MODEL_ID} / CHENGUAI_{MUSIC,VIDEO}_{ENGINE_ID,MODEL_CACHE_DIR} / CHENGUAI_MUSIC_{CHECKPOINTS_DIR,REPO_DIR,LM_MODEL,MAX_TASKS_PER_WORKER}`，并默认 `HF_HUB_OFFLINE=1`。
- `default_image_venv_python()` → `.venv-image\Scripts\python.exe`；`default_image_model_dir()` → `models/sana-multiling`；`default_video_venv_python / default_video_model_dir / default_music_venv_python / default_music_repo_dir / default_music_model_dir`：便携包相对定位。

## 3. app/engine —— 引擎实现（只在引擎 venv 内 import）

- `app/engine/abstract.py`：`AIGenerationEngine`（`generate/memory_usage/requires_cuda`）+ `ImageGenerationEngine`（`generate_image(prompt,width,height,steps,seed,negative_prompt)`、`default_steps`）+ `VideoGenerationEngine`（`generate_video(prompt,image_path,num_frames,...)`）+ `AudioGenerationEngine`（`generate_audio(prompt,duration,sample_rate)`）。注：文件尾部 `NoOpImageEngine` 为历史占位死代码，现行链路不使用（真实引擎缺失时由「不注册引擎」拒绝任务，绝不占位）。
- `app/engine/prompt_utils.py`：`coerce_prompt_text(value, *, joiner=", ") -> str | None`——把 str/bytes/任意嵌套 list/tuple/标量混排规整为合法 `str`（tokenizers 只接受标量 str，嵌套 list 会抛 `TextEncodeInput` TypeError）。
- `app/engine/image/sana.py`：`SanaImageEngine(ImageGenerationEngine)`（diffusers `SanaPipeline`，仓库 `Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers`）+ `SanaEngineError`；本地快照离线加载（fp16 三件：Gemma-2-2B 编码器 4757.0+229.5MB / Linear-DiT transformer 3060.3MB / DC-AE 32× VAE 1191.2MB，磁盘实测 9.06GB）。显存策略：**逐模块 CPU 换入**（任一时刻显存里只有当前一件，最大单件编码器 4.99GB）；空闲显存 <5120MB 自动退到**逐层换入**（慢约两三倍），<1500MB 直接拒绝并点名占用者；加载权重要求空闲物理内存 ≥16GB。尺寸对齐 32 的倍数、单边 ≤2048、单遍预算 1,100,000px、目标硬上限 4,200,000px；超预算走「底图 + Lanczos 放大」，元数据如实写 `upscaled=true`/`tiles=0`；steps 默认 20、夹取 10-40；CFG 固定 4.5；提示词原样进编码器（`prompt_translated` 恒 false），实测峰值写进元数据 `peak_vram_mb`。
- `app/engine/image/plan.py`：纯标准库的出图规划器（三端共用，不 import torch）。`SANA_PROFILE`（`SANA_MULTIPLE=32`、`SANA_MAX_SIDE=2048`、`SANA_SINGLE_PASS_MAX_PIXELS=1_100_000`、`SANA_BASE_MAX_PIXELS=1_100_000`、`SANA_MAX_TARGET_PIXELS=4_200_000`、`can_refine_tiles=False`、`base_multiple=32`）与 `resolve_output_plan(w, h, profile="sana") -> ... upscale_only`。`_PROFILES` 里还留着 `sd15` 档（8 倍数、单遍 850000px、分块重绘上限 25,000,000px），但现役注册链路只取 `sana`，`sd15` 档只服务未注册路径与 `tests/`。图生图 `wan-i2i` 的 16 倍数与像素预算不在这里，写在 `app/engine/video/budget.py`：`MAX_PIXELS = 832 * 480 * 115 // 100 = 459264`（**整数运算**；写成 `int(832*480*1.15)` 会得到 459263，1.15 是浮点、`int()` 砍小数，这正是当年三端分叉的根源）是全仓唯一一份真数，`wan.py` 的 `_MAX_PIXELS`、桌面端 `param_panel.py` 的 `VIDEO_MAX_PIXELS`（import 别名，不再本地重算，旧的 459000「预警线」已删）、前端 `app.js` 的 `VIDEO_MAX_PIXELS = 459264` 三处同一个数，由 `tests/test_mode_theme.py::test_wan_pixel_cap_is_one_number_across_three_ends` 钉住。
- `app/engine/memprobe.py`：物理内存闸门工具（纯标准库 + ctypes）：`read_physical_ram()` 用 `GlobalMemoryStatusEx` 读总量/空闲 MB、`ram_gate_ok/ram_gate_message`（空闲不足时给「谁在吃内存、该怎么办」的中文文案）、`floor_mb_or()`（环境变量临时压低门槛时的安全下限）。当初加它是因为 2026-11-08 那次 `wan-i2v` 真机尝试在加载阶段被系统杀掉、日志停在半路；Sana 与 Wan 加载前都走这道闸。
- `app/engine/image/sd15.py`、`hires.py`、`app/engine/prompt_bilingual.py`：**SD1.5 时代的代码，现已不注册**。`prompt_bilingual.to_clip_prompt`（中英关键词桥接）只被 `sd15.py` 用——Sana 原生吃中英文，现役链路不需要它；换型后它只剩 `sd15.py` 与 `tests/test_prompt_bilingual.py` 两个引用点。
- （历史）`app/engine/image/flux.py` / `app/tasks/flux_worker.py`：FLUX.1-schnell 引擎与适配器，2026-11-08 因显存铁律（峰值 ≤6GB）整套删除，代码与权重都不在仓库与盘上。
- `app/engine/video/wan.py`：`WanVideoEngine(VideoGenerationEngine)`——`wan-t2v`（`WanPipeline`）/ `wan-i2v`（`WanVACEPipeline`，首帧=垫图条件，`prepare_video_and_mask` 首帧 mask=0、其余待生成）/ **图片档只剩 `wan-i2i`**（同一 VACE 权重，`reference_images` 参考重绘单帧 PNG，480p 档、16 倍数、像素预算 459264px；文生图已由 Sana 承担，`wan-t2i` 已下架）；本地 HF 快照离线加载（fp32 VAE + bf16 pipeline + `enable_model_cpu_offload` + UniPC `flow_shift=3.0`）；尺寸 16 倍数 + 像素预算按 `budget.MAX_PIXELS`（459264）收口、帧数 `4n+1`、token 闸门见 `wan.py` 的 `_enforce_budget`（越线**直接报错**并给出具体出路，不再等比缩小画幅）、固定 16fps、官方中文负面提示词；OOM 捕获给 `release_local_engine.bat` 指引，不伪造；输出 `wan_{t2v|i2v|i2i}_{时间戳}.{mp4|png}`（<1KB 判失败）；mp4 同时写同名 `-first.png` 首帧封面（`frames_out[0][0]` 直接存，不回解 mp4、不依赖 ffmpeg；两端卡片用它当封面/灯箱 poster，写失败只把返回 dict 里的 `preview` 置空，不判任务失败）。旧 Anima adapter 已于 2026-11-05 整体删除。
- `app/tasks/video_worker.py`：Wan 视频生产适配器（`.venv-video` 子进程）；参数白名单 `width/height/num_frames/steps/seed/negative_prompt`（i2v 另加 `init_image_path`；t2v 带垫图报错引导换引擎）；health_check 查 CUDA + 快照存在（缺失提示运行 `scripts\download_video_weights.py`）；`output_files` 顺序固定 mp4 在前、首帧封面在后（存在才加）——两端取主产物都按后缀筛，封面不会被当成视频本体。

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
| `GET /outputs/<file>` | 产物预览（output_dir 与 worker 同源：优先 `config["output_dir"]`）；mp4/wav/mp3 走 `Accept-Ranges: bytes` + 单段 Range（206/416）与 inline -Disposition，网页端 `<video>`/`<audio>` 因此能在工具内播并能拖进度 |

## 5. app/ui —— 桌面端 MJ 同款三栏创作界面（仅表现层，服务来自 bootstrap 注入）

2026-11-05 重构：旧的每模态一个 `WorkspacePage` 的十页表单式桌面已删除，`MainWindow._submit_generation` 入口不复存在。现为「左会话栏 · 中创作流 · 右参数面板」三栏，与 Web 控制台逐字对齐同一套提交语义（图像每次 1 张与 seed 规则、垫图门槛、音乐显存门槛与能力确认、U/V 语义、帧数 4n+1 对齐等）、五类产物都在工具内显示与播放（图片 lightbox、视频播放窗/网页内嵌播放器、音乐卡片播放条）。

- `app/bootstrap.py`：`create_application()` 装配与 Web 相同（ConfigStore / EngineManager / TaskService / `register_production_engines`）+ `start_task_service_loop()`（后台线程 asyncio 驱动 `task_service.start()`，返回 `(停止回调, 事件循环)`；`aboutToQuit` 优雅停止）。事件循环传给 `MainWindow`，桌面端因此能直接调用 `cancel_task` / `restart_engine` 等 async 接口（`asyncio.run_coroutine_threadsafe`）。
- `app/ui/main_window.py` — `MainWindow(config_store, logger, database_path, task_service, engine_manager, service_loop)`：三栏骨架。左栏 250px（新建会话、会话列表含缩略图/改名/删除、「系统功能」导航 7 项进入 `SystemPageShell`，`QStackedWidget` 返回创作页）；右栏 320px `ParamPanel`；中部 `CreationPage`。会话与参数桶持久化在同 APP_HOME 的 `desktop_sessions.json`。
- `app/ui/session_store.py` — `SessionStore`：会话/批次（batch）JSON 持久化（tempfile + `os.replace` 原子写），上限 120 会话，缩略图路径、每模式参数桶、`refDir` 记忆。
- `app/ui/pages/creation_page.py` — `CreationPage`：五模式 Tab（t2i/i2i/t2v/i2v/music）+ 顶栏引擎/健康 pill + 队列徽标；`imagine()` 唯一提交入口：图像模式每次 1 张（2026-11-10 换 Sana 后不再 4 变体连发）、`NEEDS_REF` 垫图门槛、`_ref_path_error` 垫图文件校验（composer 与高级参数白名单两处）、音乐四重门槛（引擎未注册 / 无 GPU / 总显存 <6144MiB / 空闲 <6144MiB）+ 能力边界确认弹窗；upscale 与 variation 按源图出自哪个引擎分流（Sana 源图：同提示词按更大尺寸重出 / 原尺寸换 seed 重出；`wan-i2i` 与历史 `wan-t2i` 源图：拿源图垫图走参考重绘），各 1 张，从瓦片/lightbox 发起；retry（失败任务参数快照重投，模式回退）、cancel（经服务事件循环）、`play_artifact`（视频/音频产物在**工具内播放窗**里放，一次只留一个窗，关窗释放解码器）、引擎重启、2.5s 轮询刷新流与徽标、断点队列位置提示。7 个系统页（仪表盘/队列/历史/模型/引擎/设置/诊断）保留在同一窗口内。
- `app/ui/widgets/`：`param_panel.py`（参数面板，按模式分桶、引擎选择/状态、音乐 ready 文案）、`composer.py`（提示词框：Enter 提交、粘贴/拖放存 `refs\pasted_*.png`、垫图 chip 与本机路径编辑）、`feed.py`（批次流渲染、自动滚底、骨架瓦片；转发瓦片的 V/U/取消/重试/另存/播放信号）、`tiles.py`（图片/视频/音乐/错误瓦片与工厂，读 `rec.request.*`；视频卡封面取 `-first.png`、「▶ 播放」发 `playRequested`，音乐卡把播放失败的原因写进卡片文案）、`lightbox.py`（←/→/v/u/Esc，V/U 信号）、`media_player.py`（工具内播放窗：`QVideoWidget` + `QMediaPlayer` + `QAudioOutput`，播放/进度/音量/另存/打开文件夹，错误上屏，关窗即释放解码器；缺 `QtMultimediaWidgets` 时明确说明并可交系统播放器）、`skeleton.py`（加载扫掠动画）、`common.py`（格式化、打开文件/资源管理器、toast 中枢）。
- `app/ui/theme.py`：MJ 同款令牌（暗/亮双主题，`CHENGUAI_THEME=auto|dark|light`），`build_stylesheet(dark)` 全局 QSS；主题 4 秒轮询自动跟随系统。

## 6. 环境变量参考（worker/启动端约定）

| 变量 | 作用 |
|---|---|
| `CHENGUAI_APP_HOME` / `CHENGUAI_DATA_ROOT` | 路径根（便携包首启改写） |
| `CHENGUAI_MODEL_CACHE_DIR` / `CHENGUAI_OUTPUT_DIR` | worker 权重缓存 / 输出目录（factory 注入） |
| `CHENGUAI_SANA_ENGINE_ID` / `CHENGUAI_SANA_MODEL_ID` | Sana 文生图 worker 侧引擎 id（factory 注入） |
| `CHENGUAI_SANA_MODEL_CACHE_DIR` | Sana 权重根目录（worker 优先读它，没注入时退回 `<CHENGUAI_MODEL_CACHE_DIR>/<engine_id>`）。**注册段目前还没注入这个变量**，见上节注记 |
| `CHENGUAI_VIDEO_ENGINE_ID` / `CHENGUAI_VIDEO_MODEL_CACHE_DIR` | Wan 视频与**图生图** worker 的引擎 id 与权重目录（`wan-t2v`/`wan-i2v`/`wan-i2i` 共用这两个） |
| `CHENGUAI_MUSIC_ENGINE_ID` / `CHENGUAI_MUSIC_*` | 音乐引擎 id 与 checkpoints/repo/LM/自重启阈值 |
| `CHENGUAI_SANA_MIN_FREE_MB`（默认 1500）/ `CHENGUAI_SANA_MIN_FREE_RAM_MB`（默认 16384） | 排障用：临时压低 Sana「逐层换入」所要求的空闲显存门槛 / 加载前所要求的空闲物理内存门槛。默认值是策略，不是鼓励压低 |
| `CHENGUAI_SANA_GUIDANCE` | 覆盖 CFG（默认 4.5 = 官方示例值）；界面不开放，只用于对照实验 |
| `CHENGUAI_DISABLE_MODALITIES` | 硬件分级禁注册（`"music"` 形式）：`image` 只禁 `sana-multiling`/`wan-i2i`，`video` 只禁 `wan-t2v`/`wan-i2v`，`music` 只禁 `ace-step-15`，互不牵连 |
| `HF_HUB_OFFLINE=1` 等 | 强制离线加载本地权重（setdefault，可显式覆盖） |
| `ACESTEP_CHECKPOINTS_DIR / CONFIG / LM_MODEL / BACKEND=pt / OFFLOAD_TO_CPU=true / OFFLOAD_DIT_TO_CPU=true` | ACE-Step 1.5（12GB 档必须开 DiT offload，上游 #1337） |

（`CHENGUAI_FLUX_ENGINE_ID` 已随 FLUX 引擎于 2026-11-08 一并删除，现行代码里不存在。）

## 7. 独立复用指南（第三方只想要「本地任务队列 + 引擎 worker」）

1. `TaskService(db_path, EngineManager())` + `register_production_engines(...)`（或自注册自己的 `EngineAdapter`/`--adapter-factory` worker）。
2. 一个事件循环里 `await task_service.start()`（可与其它 asyncio 服务共存；多进程共存自动降级为只提交）。
3. `submit_task(TaskRequest(...))` → 轮询 `get_task`；取消/重试语义见上表。
   引擎侧只需实现 `EngineAdapter` 协议并（可选）放进独立 venv 经 `process_worker` 拉起。