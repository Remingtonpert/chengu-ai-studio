# 辰鹄 AI 创作中心架构提案

**状态：Phase 1 桌面骨架与 Phase 2 任务服务已实现；真实引擎尚未接入。** 本文区分已实现服务与待后续阶段完成的推理能力。

## 目标

为 Windows 本地桌面程序建立单一中文入口；生成引擎独立运行、互相隔离，离线推理，单 GPU 默认串行。每个模态可以独立未安装/未验证，不通过模拟结果伪装真实能力。

## 拟议组件

```text
辰鹄桌面程序（PySide6）
  ├─ UI 页面与本地状态展示
  ├─ TaskService：校验请求、持久化任务、FIFO、取消/重试
  ├─ EngineManager：独立进程生命周期、健康检查、错误隔离
  ├─ ModelManager：本地清单、revision/license/hash/路径/空间
  ├─ HardwareService：GPU、VRAM、RAM、磁盘观察与运行前检查
  ├─ SQLite：迁移、队列、历史记录
  └─ Engine adapters（每引擎独立 venv / 子进程）
       ├─ 图片：Diffusers + FLUX.1-schnell（候选）
       ├─ 视频：FramePack adapter（许可/API 待确认）
       └─ 音乐：ACE-Step v1.5 adapter（许可/硬件待确认）
```

目录可按职责演进，不要求一次性创建完整空壳：

```text
app/
  bootstrap.py
  core/                 # config, paths, logging, events, errors
  ui/                   # PySide6 主窗口、页面、widgets
  services/             # task, engine, model, storage, hardware
  engines/              # base protocol 与逐模态 adapter
  database/             # SQLite 与版本化 migrations
  models/               # Task / Engine / Model 数据结构
  security/             # 路径校验；若无本机 API 不引入网络服务
scripts/
tests/
docs/
```

旧 Gradio/CLI 原型（`app/web_ui.py`、`app/scheduler.py`、`app/main.py` 等含占位输出的路径）**已于重构中全部移除**（2026-10 起逐步替换，现已不存在于源码树）。当前两个入口：PySide6 桌面端 `app.bootstrap` 与本地 Web 控制台 `app.web.server`，两者共用同一服务层（模块与接口清单见 `docs/MODULES.md`），桌面依赖位于项目 `.venv`。

## 模块职责和边界

- **UI**：只提交请求、显示持久化状态和实际生成结果；不直接加载大型模型，不拥有任务真相。
- **TaskService**：请求验证、FIFO、状态转换、超时、取消、重试、输出校验和数据库事务。任务字段至少含 `task_id`、`task_type`、`engine_id`、`model_id`、提示词/输入、参数、状态、真实进度状态、时间、结果和错误。
- **EngineManager**：通过安全的 `subprocess` 参数数组启动独立 Python 环境，不拼接用户 Shell 字符串；引擎崩溃可重启，不拖垮 UI。除非确有必要，不新增 localhost 服务。
- **Engine adapter**：统一 `get_engine_info`、环境检查、模型列表、请求校验、启动/状态/取消/关闭/健康检查。每个引擎声明其真实支持参数。引擎无进度回调时只显示“处理中/进度不可用”。
- **ModelManager**：将模型根目录放在用户配置的数据盘位置；持有 provenance、许可证、revision、大小、hash、使用锁和正在使用状态。模型删除需确认。
- **HardwareService**：返回测得数据或 `unavailable`，不硬编码 RTX 3060、不伪造可用显存。内存阈值为策略，不是模型兼容承诺。
- **SQLite**：任务和历史持久化；启动时迁移。非终态任务在进程退出时转为失败/中断，并保留诊断信息，不假装完成。

## 任务生命周期与 GPU 策略

```text
queued -> preparing -> running -> completed
                   \-> failed
queued/preparing/running -> cancelled
```

默认单 GPU 同时只执行一个推理任务。先检查模型路径、任务参数和当前资源，再启动引擎。取消应先发合作式取消信号，超时才终止对应子进程并回收；不得结束不属于应用的进程。失败任务与日志写入数据库/本地日志。不得根据时间推算百分比。

## 离线和本地安全

- 联网下载与离线生成是分开的显式模式；无后台下载，无模型库在线回退。
- 将用户提供的绝对路径规范化并限制访问策略；删除结果/模型须明确确认。
- 不监听 `0.0.0.0`。优先不用网络 API；若后续必须使用，仅绑定 `127.0.0.1` 并实现访问控制。
- 不发送 prompt、输入/输出媒体、日志或遥测；禁用自动分享链接。
- Ollama 是可选本机文本辅助，不属于图像、视频或音乐引擎。

## Phase 1 实际落地

- PySide6 主窗口已具备十页真实导航；图片/视频/音乐页标明引擎未验证且禁用生成按钮。
- JSON 配置在用户 LocalAppData；模型/输出根路径默认 `D:\ChenguAI`，设置页可编辑，也支持 `CHENGUAI_DATA_ROOT` 覆盖。
- SQLite schema v2 和 migrations 已建立，启动时初始化；TaskService 已实现任务持久化和状态转换。
- 本地滚动日志已接入；仪表盘用 `nvidia-smi` 探测显卡/显存并读取输出盘剩余空间。
- Phase 1 测试通过；桌面主程序不导入 torch。

## Phase 2 实际落地

- `app/tasks/models.py` 定义任务请求、结果、记录和状态；进度未知时保持 `NULL`。
- `TaskService` 负责 SQLite 队列、FIFO、任务状态、取消、超时、失败、重试、启动恢复和关闭。
- `EngineManager` 懒启动 adapter，执行健康检查并支持单引擎停止/重启。
- `ProcessEngineAdapter` 通过固定 argv 启动 worker，以 JSONL IPC 传输请求/结果；不把用户文本拼入 shell 命令。进程崩溃转换为任务失败，主窗口继续运行。
- 队列、历史和引擎管理页读取真实本地状态。未注册真实引擎，UI 不允许提交生成请求。
- 测试适配器只在 `tests/` 定义，结果带 `test_only` 标识并且不产出媒体文件。

## Phase 3 实际落地（2026-11-05 更新）

- 真实引擎已接入并经 `ProcessEngineAdapter` JSONL IPC 运行在独立 venv：FLUX.1-schnell 图像与 Anima 视频（`.venv-image`）、ACE-Step 1.5 音乐（`.venv-music`）；权重按 SHA-256/safetensors 逐文件校验，主进程零 torch。
- FLUX 已产出多张真实图（英文提示词批 + Web 产物预览实测 200）；中文提示词端到端出图、ACE-Step 端到端音频的真机验收脚本已就位（`scripts\acceptance_flux.ps1` 含中文步骤），执行排队等待显存窗口。
- 桌面端三模态生成按钮已接通共享 TaskService（与 Web 同一队列/同一 worker 协议）；TaskService 增加跨进程执行者锁、原子领取与 5 秒轮询，两模式可同时运行。
- 全部模块与接口的权威清单见 `docs/MODULES.md`；根 `README.md` 为进度真相源。

## 历史实现差距（2026-08 源码审查记录 · 现状标注）

> 以下为重构启动前观察到的旧原型问题，保留作审计记录；**全部已解决或随旧代码移除**：

- ~~`app/web_ui.py` 捕获模型异常后构造灰色占位图~~ → 文件已删除；现行 Web 控制台（`app/web/server.py`）绝不生成占位图，失败任务诚实带 `error_message`。
- ~~`app/scheduler.py` `asyncio.sleep` 模拟完成、硬编码可用显存~~ → 文件已删除；现行 `TaskService`/`app.core.hardware` 只报告真实测得数据或 unavailable。
- ~~旧 `app/engine/image/flux.py` 下载失败返回占位结果~~ → 已重写：离线快照加载（`HF_HUB_OFFLINE=1`）、生成前输出目录解析、空/异常提示词诚实报错，绝不占位。
- ~~`app/main.py`、`requirements.txt` 重复冲突~~ → 已清理；入口唯一（桌面 `app.bootstrap`、Web `app.web.server`）。
- ~~无生产 adapter / 真实引擎未实现~~ → 已实现三模态生产 adapter 与两个独立 venv worker（见上节）。

结论修订：桌面与 Web 两端现在都提供真实本地 AI 生成（引擎注册即权重与依赖已校验）；“未验证即禁用”的安全策略由**引擎注册门槛**实现——权重缺失或许可证未确认（如 Anima 视频权重待用户决策）时引擎不注册，UI 与 API 拒绝提交，全程无模拟输出。

## 设计决策门槛

1. 用户确认 Phase 0 候选和许可风险；未确认不下载大型模型。
2. 用独立环境验证 PySide6 主程序与选择的 Python 版本。
3. 以真实 Windows 安装、GPU 推理和断网复测确定引擎；有疑问的候选留为未安装状态。
4. 只有实际跑通后才锁定依赖、模型 revision、参数范围和发布架构。

## 阶段门槛

1. 测试适配器只验证生命周期，不进入应用引擎注册表或生成 UI。
2. 注册真实 adapter 时使用 `ProcessEngineAdapter` 和应用定义的固定命令参数；IPC 只传 JSON，不执行用户 prompt 或路径中的命令。
3. 下载模型前确认许可、revision、文件大小、校验方式和磁盘余量。
4. 每个真实引擎通过目标 Windows 推理和断网测试后，才能在 UI 中标记可用。