# 辰鹄 AI 创作中心开发计划
> **2026-11-10 更新（读前必看）**：文生图引擎现职为 **Sana-1.6B 多语言版**（引擎 id `sana-multiling`，磁盘 9.06 GB，`.venv-image` 独立 worker），图生图为 **Wan2.1-VACE-1.3B**（引擎 id `wan-i2i`，与视频共用 `.venv-video` 与 `models\wan2.1`）。历史上的 FLUX.1-schnell（2026-11-08 整套删除）、SD1.5 精调 RealVis（`sd-realvis` / `sd-realvis-i2i`，磁盘 4.41 GB，真机峰值 1.88-2.09 GB，**2026-11-09 起不再注册、留盘作后备**）、`wan-t2i`（Wan2.1 单帧出图，2026-11-10 下架）**都不是现役能力**；本文中把它们当作现役的段落属于**历史记录**。选型与许可结论见 `docs\ENGINE_RESEARCH.md` 文末「2026-11-10 文生图再次选型」、`LICENSES.md`、`docs\LICENSE_AUDIT.md` 文末增补。三条铁律：**可商用 / 中英文提示词 / 显存峰值 ≤6GB**。

**计划状态（2026-11-10 更新）：Phase 1、Phase 2 完成验收；Phase 3 进行中——现役三模态引擎为 Sana 文生图（`sana-multiling`）、Wan2.1 文生视频 / 图生视频（`wan-t2v` / `wan-i2v`）与图生图（`wan-i2i`）、ACE-Step 1.5 音乐（`ace-step-15`）。FLUX.1-schnell 曾接入并本机真实出图（多批成功产物 + 离线复现），因显存铁律于 2026-11-08 整套删除；SD1.5 三件套同日接手、现退居未注册后备；Anima 引用已于 2026-11-05 整体移除。Wan 视频真机峰值已实测（2021 MB）；Sana 与 ACE-Step 的端到端真机验收仍排队等显存窗口，通过前不标记已完成。旧原型的虚假占位输出路径已全部移除，现行代码无任何模拟生成。当前进度真相源：根 `README.md`。**  
本计划遵循阶段验收；未完成硬件实测的能力不标记为已完成，不安装全局依赖，不在确认前下载大型模型。

## Phase 0：调研与规划

**本轮交付：**

- [ENGINE_RESEARCH.md](ENGINE_RESEARCH.md)：官方候选、系统要求、已知缺口与推荐路线。
- [LICENSE_AUDIT.md](LICENSE_AUDIT.md)：代码与权重分开审查，记录商业限制和待核实项。
- [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md)：本机环境观测、厂商声明与待实测条件分开。
- [ARCHITECTURE.md](ARCHITECTURE.md)：目标职责边界及现有原型差距。
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)：阶段/验收计划（本文件）。

**结论（Phase 0 当时的历史结论，已被后续实测推翻）：** 图片优先候选 FLUX.1-schnell；视频 FramePack、音乐 ACE-Step v1.5 仅为有条件候选。权重文件大小、revision、部分模型许可和目标显卡实测尚未完成。本地代码已有虚假占位输出、模拟任务完成等阻断问题。Phase 0 已做静态官方资料核查与环境读取，但候选许可缺口需用户接受继续核实；不代表软件已可用。**后续演变**：FLUX 因显存出局并整套删除；FramePack 让位于 Anima、Anima 又因上游非商业许可整体移除，视频收口为 Wan2.1；音乐候选 ACE-Step v1.5 至今仍在岗；图片最终由 Sana-1.6B 多语言版接任（2026-11-10）。

## Phase 1：桌面骨架和本地基础

**范围：** PySide6 主窗口与真实页面导航、配置/路径服务、日志、SQLite 和迁移、启动/关闭处理、可复现的项目虚拟环境。

**结果：完成（2026-10-11）。** 新增 PySide6 窗口、10 页导航、JSON 配置、SQLite v1 migration、轮转本地日志、GPU/输出盘探测、PowerShell 启动脚本和项目 `.venv`。设置页可配置模型/输出目录。引擎未验证时生成按钮禁用；本阶段未安装推理包或下载模型。

**验收：** `tests/test_phase1_foundation.py` 5 项通过，包括配置读写/非法值、数据库幂等初始化、十页切换与生成禁用、bootstrap 创建 DB/log 并正常退出。旧 FLUX 测试在未安装 torch 的基础 venv 中按条件跳过。

## Phase 2：引擎与任务系统

**范围：** 统一 engine/task 数据结构、FIFO、状态迁移、取消、超时、持久化、异常隔离、日志和进程清理。只使用仅供测试且在界面明确标注的测试适配器。

**结果：完成核心生命周期（2026-10-11）。** 新增 SQLite v2 迁移（重试关系、结果元数据）、TaskRequest/TaskResult/TaskRecord/TaskStatus、持久化 FIFO TaskService、合法状态转换、queued/active cancel、超时、失败记录、显式 retry、启动恢复和安全关闭。已验证既有 v1 数据库任务可无损升级。EngineManager 可懒启动、健康检查、停止、重启；ProcessEngineAdapter 用固定 argv 和 JSONL stdin/stdout IPC 将引擎 worker 放到独立子进程，异常退出不会直接终止桌面主进程。Qt 队列/历史页读取 SQLite；无真实引擎注册，生成按钮仍禁用。

**验收：** `tests/test_task_service.py` 14 项通过，覆盖 FIFO、v1→v2 无损迁移、状态持久化、取消、失败/重试、超时、重启恢复、排队任务续跑、适配器启动/停止/重启、健康检查失败清理、子进程 IPC 与崩溃回收、无输出禁止成功、清理异常不阻断队列。测试适配器仅定义在测试目录，明确 `test_only` 且不生成媒体。进度字段保持 `NULL`，没有虚构百分比。全仓 `pytest -q` 最终结果为 19 passed、1 skipped；跳过项是需要独立 torch 推理环境的旧 FLUX 测试。

## Phase 3：首个真实引擎（需用户确认）

**当时的建议候选（历史记录）：** FLUX.1-schnell + Diffusers，独立 venv/进程。开始前重新核对官方权重 revision、许可证、依赖矩阵、预计文件大小和磁盘空间；先单独测试再接入 UI。**实际路线**：FLUX 已按此接入并本机出图，但因「显存峰值 ≤6GB」于 2026-11-08 出局（23.7 GB 权重、FP8 仍需 12 GB 以上），先换 SD1.5 三件套、2026-11-09/11-10 两次重新选型后由 **Sana-1.6B 多语言版**接任文生图，图生图由 **Wan2.1-VACE-1.3B**（`wan-i2i`）承担。独立 venv/进程这一条自始至终不变（`.venv-image` / `.venv-video` / `.venv-music`）。

**验收：** Windows RTX 3060 生成有效图片；记录 VRAM、RAM、环境、参数、耗时和文件；取消/失败可恢复；断网后使用本地缓存成功推理；检查没有外部推理/API请求。**现役验收载体**：`scripts\acceptance_image.ps1/.bat`（Sana，两档 1024×1024 与 1920×1088，逐档打印实测 `peak_vram_mb`，超 6000 MB 立即停止；门槛空闲显存 ≥6144 MB 且空闲内存 ≥16000 MB）、`scripts\acceptance_video.ps1`（Wan，已实测 2021 MB）、`scripts\acceptance_music.ps1`、链式重挂 `scripts\acceptance_chain.ps1`（依序 image → video → music）。旧的 `acceptance_flux.*` / `acceptance_sd.*` 已随引擎下架删除，不再存在。

## Phase 4：视频与音乐（逐个批准）

- 视频候选 FramePack：完成权重许可、来源、独立环境、程序调用/取消/真实进度接口审查后再定。
- 音乐候选 ACE-Step v1.5：先确认 v1.5 权重许可和 Windows/12GB 实测；不可援引旧版 8GB 信息替代。
- 每次只接一个引擎。若许可或硬件门槛不通过，保留“不可用/未验证”状态，不做模拟生成。

## Phase 5：模型管理和离线部署

**范围：** 用户自选模型根目录和输出目录；模型添加、license/revision/hash/大小/路径清单、磁盘预检、下载前确认、校验、模型占用保护；所有生成在本机完成。

**验收：** 缓存齐全时断网启动和生成通过；缺文件时明确报错且不联网回退；删除模型/输出需单独确认；依赖与模型准备清单可复现。

## Phase 6：系统测试与发布

覆盖配置/迁移、任务状态/队列/取消、引擎崩溃、路径、空间不足、GPU 不可用、VRAM 不足、hash 错误、离线启动/推理、重启、有效媒体播放/打开、无远程服务暴露及许可证清单。未执行测试明确标记。发布包含启动/停止/诊断/卸载说明，卸载不得删除用户数据。

## 当前阻断项

### 现状（2026-11-10 更新）

1. **Sana 文生图的真机显存峰值仍未测得**。策略、闸门与验收脚本都已就位（`scripts\acceptance_image.bat`：门槛空闲显存 ≥6144 MB 且空闲内存 ≥16000 MB，两档 1024×1024 / 1920×1088 逐档打印实测 `peak_vram_mb`，超 6000 MB 立即停止），阻塞与历史上一样是本机资源：常驻本地引擎占约 11GB 显存 + 42GB 内存，须由用户运行 `scripts\release_local_engine.bat` 释放后自动执行。**「≤6GB」目前是按 fp16 权重体积推算的预期值，不得写成实测结论。**
2. ~~中文提示词端到端出图（旧 FLUX/SD1.5 验收脚本 `scripts\acceptance_flux.ps1`）~~ → 脚本已随引擎下架删除；现役链路为 `scripts\acceptance_image.ps1/.py`（中文提示词直接进 Sana 编码器，无桥接）+ `scripts\acceptance_music.ps1/.bat` + 链式 `scripts\acceptance_chain.ps1`（依序 image → video → music，自动重挂）。
3. ~~Anima 视频权重是否下载待用户决策~~ → 2026-11-05 决议收口：Anima 引用整体移除（真实上游非商业许可、原引用仓库不存在）；视频选型经用户确认为 **Wan2.1 1.3B**（官方不存在 I2V-1.3B 档，图生视频用 VACE-1.3B 形态）：`Wan-AI/Wan2.1-T2V-1.3B-Diffusers`（`wan-t2v`）+ `Wan-AI/Wan2.1-VACE-1.3B-diffusers`（`wan-i2v`），均 Apache-2.0，已下载至 `models\wan2.1`（物理 44.18 GB）并注册；**真机端到端已实测（2026-11-08，512×384、49 帧、12 步、逐层卸载，峰值 2021 MB / 378.7 s）**。
4. 图生图选型收口：2026-11-09 起由 **`wan-i2i`**（Wan2.1-VACE-1.3B 参考重绘单帧）承担，与视频共用 `models\wan2.1` 与 `.venv-video`，零新增下载；**2026-11-10 文生图再次换型为 Sana-1.6B 多语言版**（新增权重 `models\sana-multiling` 9.06 GB，下载脚本 `scripts\download_sana_weights.py`，走 hf-mirror、只取 fp16），旧 `wan-t2i` 下架但历史任务记录不删。
5. **待复核项**：Sana 的 DC-AE 32× VAE 上游仓库（`mit-han-lab/dc-ae-f32c32-sana-1.0-diffusers`）许可标注尚未核验；Gemma-2-2B-IT 编码器的条款文本需在随包分发前落到 `THIRD_PARTY_NOTICES.md`；Sana 权重缺逐文件 SHA-256 登记。三项都属发布前必做，见 `docs\LICENSE_AUDIT.md` 文末「2026-11-10 增补」。
6. **配置层隐患（代码侧待补）**：`ConfigStore.defaults` 里没有 `image_venv_python` / `image_model_dir` / `music_*` 这些键，而 `engine_registration` 会读它们；`_validate()` 只遍历 defaults，因此手工写进 `config.json` 的这些键会在下一次 `save()` 时被静默删掉。另外 `sana_worker` 依赖 `CHENGUAI_SANA_MODEL_CACHE_DIR` 定位权重，注册段目前只注入引擎 id、没注入该路径，把权重放在非默认位置时会退回 `<models_dir>/<engine_id>`。两处都需要代码侧修，改文档解决不了。
7. 便携完整打包（`scripts\build_portable.ps1`）已具备但尚未执行全量打包，是否现在做待用户确认。
8. ~~`models\ace-step-1.5\.cache`（约 3.9GB 重复缓存）可否清理~~ → 2026-11-05 已删除（全部为 `.incomplete` 残片），四个 safetensors 头部与张量清单复核完好。
9. Git 仓库已建立（本地提交身份 Remingtonpert）；是否 push/转公开由用户决定。

### 历史阻断项（2026-10-11 记录，均已解决，保留作审计）

1. ~~仓库不是 Git 工作树~~ → 现已是 Git 仓库，全部改动按功能分 commit。
2. 系统 Python 为 3.14.7；Phase 1 项目环境已另建 Python 3.11.9 venv。~~推理依赖矩阵仍未验证~~ → `.venv-image`（torch cu130 + transformers 5.x）与 `.venv-music`（transformers<4.58 隔离）已建成并真实推理。
3. ~~旧 Web/FLUX 路径仍会返回占位图，旧调度器用 sleep 模拟完成~~ → 旧原型文件已全部删除；现行代码失败诚实报错，无任何模拟生成。
4. ~~图片/视频/音乐真实引擎仍未实现；桌面没有任务提交控件~~ → FLUX 已本机出图；ACE-Step 权重校验就位；桌面端 MJ 同款界面五 Tab 全部可提交。
5. ~~模型文件精确大小/revision 未取得~~ → FLUX 快照 23 文件 33.73GB、ACE-Step 四文件 10.03GB 已逐文件 SHA-256 校验；FramePack 让位于 Anima（权重待用户决策）。
6. ~~旧 FLUX 测试在基础 venv 刻意不含 torch 时跳过~~ → 旧测试已移除；验收改由 `.venv-image` 真机脚本承担。

## 启动桌面程序与测试

```powershell
.\scripts\run_app.ps1
```

干净环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\scripts\run_app.ps1
```

配置在 `%LOCALAPPDATA%\ChenguAIStudio`；模型与输出目录默认是 `D:\ChenguAI\Models` 和 `D:\ChenguAI\Output`，可在设置页或通过 `CHENGUAI_DATA_ROOT` 修改。不会自动创建模型权重或下载模型。

## 下一确认门槛

Phase 2 核心任务生命周期已验证。Phase 3 现状（2026-11-10）：文生图首引擎路线历经三代——FLUX.1-schnell（已删除）、SD1.5 三件套（未注册后备）、**Sana-1.6B 多语言版（`sana-multiling`，现役）**；三者都遵循「独立 venv + 子进程 + 断网缓存 + 逐模块换入」的同一套约束。Wan2.1-T2V-1.3B / VACE-1.3B（Apache-2.0）承担文生视频、图生视频与**图生图**（`.venv-video` 独立 worker、`models\wan2.1` 权重、前端显存预检与模式↔引擎一一对应），视频真机端到端已实测（峰值 2021 MB）；ACE-Step 1.5 音乐引擎已集成、权重就绪，端到端验收与 Sana 中文出图验收一同排队等显存释放窗口。任务服务已跨进程加固（执行者锁 + 原子领取），桌面与 Web 双模式共用同一引擎与队列。

2026-11-05 同日推进（当时清单执行，保留作审计）：

- **引擎环境统一**：注册逻辑对失效的 `image_venv_python`/`music_venv_python` 配置告警并回退项目默认 venv，worker 不会再漂到系统 Python；旧「系统 Python FLUX worker」确认为旧代码进程遗留并已清除。
- **垫图必须是具体文件**：桌面 composer/高级参数、网页前端、Web 后端三层拦截目录、不存在路径与不支持格式，提示「请选择具体图片文件，不是文件夹」；后端通过后归一化路径，引擎侧报错保留为最后防线。
- **桌面端 MJ 同款三栏界面**：会话栏 + 五 Tab 创作流 + 参数面板重构完成，与网页端逐字对齐提交语义；旧 WorkspacePage 十页表单删除；音乐引擎注册后显示明确 ready 文案。
- **验证记录**：`scripts\smoke_desktop_generation.py` 冒烟全绿（含垫图目录拦截/有效文件放行新断言）；`pytest` 31 passed + 1 skipped；`node --check` 通过；改动已按功能分 commit（未 push）。
- **待用户动作（2026-11-10 口径）**：运行 `scripts\release_local_engine.bat` 释放显存窗口后，`scripts\acceptance_chain.ps1` 会按 image → video → music 自动完成 Sana 出图（含中文提示词）与音乐端到端真机验收；便携打包、`.cache` 清理（已做）、push/转公开仍等确认。文生图与视频/音乐共用同一条 FIFO 串行队列，一次只跑一个任务，界面上的排队与「前面还有 N 项」文案照实显示。