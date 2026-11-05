# 辰鹄 AI 创作中心开发计划

**计划状态（2026-11-05 更新）：Phase 1、Phase 2 完成验收；Phase 3 进行中——FLUX.1-schnell 已接入且本机真实出图（RTX 3060 多批成功产物 + 离线复现），ACE-Step 1.5 已接入（权重 10.03 GB 校验通过【权重就绪】），Anima 已注册（权重待用户决策下载）；旧原型的虚假占位输出路径已全部移除，现行代码无任何模拟生成。当前进度真相源：根 `README.md`。**  
本计划遵循阶段验收；未完成硬件实测的能力不标记为已完成，不安装全局依赖，不在确认前下载大型模型。

## Phase 0：调研与规划

**本轮交付：**

- [ENGINE_RESEARCH.md](ENGINE_RESEARCH.md)：官方候选、系统要求、已知缺口与推荐路线。
- [LICENSE_AUDIT.md](LICENSE_AUDIT.md)：代码与权重分开审查，记录商业限制和待核实项。
- [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md)：本机环境观测、厂商声明与待实测条件分开。
- [ARCHITECTURE.md](ARCHITECTURE.md)：目标职责边界及现有原型差距。
- [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)：阶段/验收计划（本文件）。

**结论：** 图片优先候选 FLUX.1-schnell；视频 FramePack、音乐 ACE-Step v1.5 仅为有条件候选。权重文件大小、revision、部分模型许可和目标显卡实测尚未完成。本地代码已有虚假占位输出、模拟任务完成等阻断问题。Phase 0 已做静态官方资料核查与环境读取，但候选许可缺口需用户接受继续核实；不代表软件已可用。

## Phase 1：桌面骨架和本地基础

**范围：** PySide6 主窗口与真实页面导航、配置/路径服务、日志、SQLite 和迁移、启动/关闭处理、可复现的项目虚拟环境。

**结果：完成（2026-10-11）。** 新增 PySide6 窗口、10 页导航、JSON 配置、SQLite v1 migration、轮转本地日志、GPU/输出盘探测、PowerShell 启动脚本和项目 `.venv`。设置页可配置模型/输出目录。引擎未验证时生成按钮禁用；本阶段未安装推理包或下载模型。

**验收：** `tests/test_phase1_foundation.py` 5 项通过，包括配置读写/非法值、数据库幂等初始化、十页切换与生成禁用、bootstrap 创建 DB/log 并正常退出。旧 FLUX 测试在未安装 torch 的基础 venv 中按条件跳过。

## Phase 2：引擎与任务系统

**范围：** 统一 engine/task 数据结构、FIFO、状态迁移、取消、超时、持久化、异常隔离、日志和进程清理。只使用仅供测试且在界面明确标注的测试适配器。

**结果：完成核心生命周期（2026-10-11）。** 新增 SQLite v2 迁移（重试关系、结果元数据）、TaskRequest/TaskResult/TaskRecord/TaskStatus、持久化 FIFO TaskService、合法状态转换、queued/active cancel、超时、失败记录、显式 retry、启动恢复和安全关闭。已验证既有 v1 数据库任务可无损升级。EngineManager 可懒启动、健康检查、停止、重启；ProcessEngineAdapter 用固定 argv 和 JSONL stdin/stdout IPC 将引擎 worker 放到独立子进程，异常退出不会直接终止桌面主进程。Qt 队列/历史页读取 SQLite；无真实引擎注册，生成按钮仍禁用。

**验收：** `tests/test_task_service.py` 14 项通过，覆盖 FIFO、v1→v2 无损迁移、状态持久化、取消、失败/重试、超时、重启恢复、排队任务续跑、适配器启动/停止/重启、健康检查失败清理、子进程 IPC 与崩溃回收、无输出禁止成功、清理异常不阻断队列。测试适配器仅定义在测试目录，明确 `test_only` 且不生成媒体。进度字段保持 `NULL`，没有虚构百分比。全仓 `pytest -q` 最终结果为 19 passed、1 skipped；跳过项是需要独立 torch 推理环境的旧 FLUX 测试。

## Phase 3：首个真实引擎（需用户确认）

**建议候选：** FLUX.1-schnell + Diffusers，独立 venv/进程。开始前重新核对官方权重 revision、许可证、依赖矩阵、预计文件大小和磁盘空间；先单独测试再接入 UI。

**验收：** Windows RTX 3060 生成有效图片；记录 VRAM、RAM、环境、参数、耗时和文件；取消/失败可恢复；断网后使用本地缓存成功推理；检查没有外部推理/API请求。

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

1. 仓库不是 Git 工作树，本轮无法比较/恢复版本控制变更。
2. 系统 Python 为 3.14.7；Phase 1 项目环境已另建 Python 3.11.9 venv。推理依赖矩阵仍未验证。
3. 旧 Web/FLUX 路径仍会返回占位图，旧调度器用 `sleep` 模拟完成；新桌面 UI 不调用这些代码。
4. 图片/视频/音乐真实引擎仍未实现；桌面没有任务提交控件，测试适配器只用于测试。
5. 模型文件精确大小/revision 未取得；FramePack 权重许可及 ACE-Step v1.5 权重许可待核实。
6. 旧 FLUX 测试在基础 venv 刻意不含 torch 时跳过；推理环境须在真实引擎阶段单独配置。

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

Phase 2 核心任务生命周期已验证。Phase 3 现状（2026-11-05）：FLUX.1-schnell 首引擎路线已获用户确认并完成集成，本机真实推理与离线复现已产出图片证据；ACE-Step 1.5 音乐引擎已集成、权重就绪，端到端验收与中文提示词出图验收正在排队；Anima 视频权重下载仍处「等用户确认」状态，未确认前不下载、视频能力保持诚实报错。任务服务已跨进程加固（执行者锁 + 原子领取），桌面与 Web 双模式共用同一引擎与队列。