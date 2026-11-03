# 辰鹄 AI 创作中心（Chengu AI Studio）

> 本地化 AI 图片/视频/音频生成平台

## 📊 项目状态概览

| 阶段 | 状态 | 描述 |
|------|------|------|
| **Phase 0** | ✅ 已完成 | 引擎调研、许可证审计、硬件兼容性分析、架构提案、开发计划 |
| **Phase 1** | ✅ 已完成 | 桌面骨架与本地基础（PySide6 UI、SQLite、配置服务） |
| **Phase 2** | ✅ 已完成 | 任务系统核心（FIFO 队列、状态管理、引擎生命周期隔离） |
| **Phase 3** | 🚧 进行中 | 真实引擎已接入：FLUX.1-schnell 图像与 Anima 视频引擎已注册（`.venv-image` 独立 worker 子进程）；FLUX 权重（约 31.4 GB）已就位并 SHA-256 校验通过；真机验收进行中 |

**重要提示**：图片与视频引擎已**接入**本地 worker（FLUX.1-schnell、Anima），FLUX.1-schnell 权重已就位并通过 SHA-256 逐文件校验；**真机验收**（RTX 3060 端到端生成、取消/失败恢复、离线推理）完成前，桌面端生成按钮保持禁用，这是符合设计的安全策略。音乐引擎尚未接入。程序不会产生占位图或模拟输出；验收通过前请勿将任何结果视为正式产出。桌面界面跟随 Windows 系统深/浅色主题自动切换。

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
│   │   └── image/              # 图片引擎 adapter（FLUX 候选）
│   │       ├── __init__.py
│   │       └── flux.py         # FLUX.1-schnell adapter 占位
│   ├── models/                  # 模型管理（待 Phase 5 实现）
│   │   ├── __init__.py
│   │   ├── manager.py          # 模型清单、校验与下载确认
│   │   └── registry.py         # 模型注册表
│   ├── tasks/                   # 任务系统核心
│   │   ├── __init__.py
│   │   ├── engine_manager.py   # 引擎生命周期管理（懒启动、健康检查）
│   │   ├── flux_worker.py      # FLUX worker 工厂（测试适配器）
│   │   ├── models.py           # TaskRequest/TaskResult/TaskRecord/TaskStatus
│   │   ├── process_adapter.py  # ProcessEngineAdapter 子类定义
│   │   ├── process_worker.py   # Worker 协议实现与 JSONL IPC
│   │   └── service.py          # FIFO 任务队列、状态转换、取消/重试
│   ├── ui/                      # PySide6 界面层
│   │   ├── __init__.py
│   │   ├── main_window.py      # 主窗口与十页导航
│   │   └── pages/              # 页面组件
│   │       ├── __init__.py
│   │       ├── engine_status_page.py
│   │       ├── settings_page.py
│   │       ├── task_list_page.py
│   │       └── workspace_page.py
│   └── utils/                   # 工具函数
│       └── config.py
├── tests/                       # 测试目录
│   ├── test_flux_image.py
│   ├── test_flux_worker_adapter.py
│   ├── test_phase1_foundation.py
│   └── test_task_service.py
├── docs/                        # 技术文档
│   ├── ARCHITECTURE.md         # 架构提案与模块边界
│   ├── DEVELOPMENT_PLAN.md     # 阶段验收计划
│   ├── ENGINE_RESEARCH.md      # 引擎候选调研
│   ├── HARDWARE_COMPATIBILITY.md # 硬件兼容性说明
│   └── LICENSE_AUDIT.md        # 许可证审计记录
├── scripts/                     # 部署脚本
│   └── run_app.ps1             # Windows PowerShell 启动脚本
├── .venv/                       # PySide6 桌面程序虚拟环境（Python 3.11）
├── .venv-image/                 # 图片引擎专用虚拟环境（待安装 torch/diffusers）
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
| `CHENGUAI_DATA_ROOT=<path>` | 覆盖默认数据根目录（模型/输出目录） |