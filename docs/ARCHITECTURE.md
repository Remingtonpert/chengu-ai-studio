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

仓库仍保留旧 Gradio/CLI 原型；新的 PySide6 桌面入口为 `app.bootstrap`，桌面依赖位于项目 `.venv`。

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

## 当前实现差距（已从本地源码观察）

- `app/web_ui.py` 捕获模型异常后构造灰色占位图；异常被显示为“生成中”，会伪造成功感。
- `app/scheduler.py` 对任务执行 `asyncio.sleep` 后标记完成，GPU 检测函数引用未导入的 `torch`，异常时还返回硬编码的可用显存。
- `app/engine/image/flux.py` 下载失败后会返回占位结果，输出路径与调用参数存在不一致；引擎抽象与 UI 调用不统一。
- `app/main.py`、`requirements.txt` 有重复/冲突内容；文档宣称桌面端和模型能力完成，但当前代码没有对应实现。
- 当前没有生产 adapter 或本地推理子环境，子进程 host 只通过 test-only worker 做协议测试。
- 图片/视频/音乐真实引擎、模型校验和断网推理仍未实现。旧 Web/CLI 路径仍可能伪造占位输出，不属于新桌面任务服务。

因此桌面程序可以验证任务生命周期，但不能声称已经提供 AI 生成。后续接入真实引擎前，应禁用或移除旧 Web/CLI 的占位输出路径。

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