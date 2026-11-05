# 辰鹄 AI 创作中心：硬件兼容性评估

**核查日期：2026-10-11（调研快照）**  
**状态：当时候选模型均未执行真实推理；此前提已过期——2026-11 起 FLUX.1-schnell 已在本机 RTX 3060 12GB 完成真实推理并产出图片（512/1024/1216×704 各档，显存/耗时如实记录），ACE-Step 1.5 权重校验就位、端到端验收排队中；Anima 未推理（权重待用户决策）。资料声明不等于本项目兼容认证；当前实测数据见根 `README.md`。**

## 当前开发机观测

| 项目 | 实际观测 | 说明 |
|---|---|---|
| 操作系统 | Windows；用户目标为 Windows 11 64 位 | 本次命令未收集 Windows edition/build |
| Python | 系统 3.14.7；项目三套 venv 已建成（2026-11-05）：`.venv` 3.11.9（PySide6）、`.venv-image`（torch cu130 + diffusers + transformers 5.x，FLUX/Anima worker）、`.venv-music`（transformers<4.58，与 image 环境硬冲突故隔离） | 推理只发生在后两个 venv 的子进程；主进程零 torch。引擎注册对失效的 venv 配置会告警并回退默认环境 |
| GPU | NVIDIA GeForce RTX 3060，WDDM | 设备可见 |
| 显存 | 12,288 MiB 总量；命令执行时约 2,501 MiB 已占用 | 空闲量会随桌面应用变化；模型预算不得假设全部 12GB 可用。FLUX 真实出图时的显存/内存实测记录见根 `README.md` 验收数据（worker 权重加载约 31-34GB RAM）；若本机有其他常驻引擎占用显存，验收脚本会自等待而非硬撞 |
| Driver/CUDA 信息 | NVIDIA-SMI 显示 Driver 617.14、CUDA UMD 13.4 | 这是驱动报告，不代表已安装对应 CUDA Toolkit，也不能决定 PyTorch wheel；应使用 PyTorch 官方安装矩阵 |
| RAM/磁盘 | 64 GB RAM（2026-11-05 复核）；输出盘 `D:\ChenguAI\Output` | 模型库占用：FLUX 快照 33.73 GB + ACE-Step 10.03 GB（另有 `.cache` 残留约 3.9GB 待确认清理） |

## 兼容性判定规则

- 12GB 是设备标称总显存，不是应用可用预算。Windows 桌面和其他 GPU 进程会占用显存；应记录模型加载前、峰值和结束后的可用/已用显存。
- 仅当官方记录了目标任务设置或目标机实测时才填写显存数字。权重参数量乘精度大小不是峰值显存；激活、缓存、编码器和加载副本均会增加用量。
- CPU offload、量化和低分辨率属于有条件的优化路线，不是兼容性证明。每次报告应包含权重 revision、dtype/量化方法、分辨率、帧数/时长、步数、峰值 VRAM、RAM、耗时、结果文件和日志。
- 所有 GPU 生成任务默认串行；至少预留桌面及显示所需显存。开始任务前若安全余量不足，应排队/拒绝并解释，不要直接尝试加载。
- Python、PyTorch、CUDA runtime、NVIDIA 驱动和每个扩展（如 Triton/xformers/Flash Attention）必须作为一个组合验证。优先使用 PyTorch 官方 Windows wheel；不在系统 Python 上修改全局依赖。

## 候选硬件证据与结论

| 候选 | 官方硬件说明 | RTX 3060 12GB 结论 |
|---|---|---|
| FLUX.1-schnell + Diffusers | Diffusers 官方 FLUX 文档提供 sequential/model CPU offload 和量化示例；说明 Flux 大型组件完整加载约 50GB，低 VRAM 优化示例不是目标机基准；FP8 可能受 GPU/CUDA/PyTorch 组合影响。 | **已实测通过（2026-11）**：RTX 3060 12GB 上 512×512、1024 档、1216×704 均产出真实图片，`HF_HUB_OFFLINE=1` 离线复现成功，显存/耗时如实记录（根 `README.md`）。中文提示词端到端属脚本已就位的排队验收项。 |
| FramePack | 官方 README 声明支持 NVIDIA RTX 30XX 且至少 6GB；宣称 13B 模型在最低 6GB 设备上工作，较慢显卡速度下降。官方 Windows release 使用 CUDA 12.6/PyTorch 2.6；模型下载超过 30GB。 | 未采用；视频候选已转向 Anima（`app/engine/video/anima.py`，共用 `.venv-image`），其权重为社区类许可，下载与否待用户决策，未推理。 |
| 原始 HunyuanVideo | 官方主仓库列出 544×960×129 帧 45GB、720p×129 帧 60GB 峰值，并说明在 80GB GPU 测试、测试系统 Linux。FP8 约节省 10GB 的官方表述不能直接代表总需求降至 12GB。 | 排除原始推荐配置。FramePack 是独立低显存候选，不可把其项目声明归因于 Hunyuan 原生推理。 |
| ACE-Step v1.5 | 官方仓库测试表包含 RTX 4090、A100、RTX 3090、M2 Max；未见 RTX 3060 v1.5 实测。README 的 8GB 优化公告在 v1.5 发布前。 | 环境已建成：`.venv-music`（transformers<4.58）+ 权重 10.03GB 校验 + 12GB 档必需的 DiT offload 环境变量（上游 #1337）；提交门槛按实测设 12288MiB 总量 / 7000MiB 空闲。**端到端出音频的真机验收仍排队等显存窗口，通过前不标记「已验证」。** |
| MusicGen | 官方 MusicGen 模型卡提供 300M/1.5B/3.3B 规格，未以此证明 Windows 12GB 组合。 | 硬件可能性不是主要阻塞；权重 CC-BY-NC 4.0 的非商用条款是商业路线的阻塞项。 |

## Windows / 依赖现状

- 当前 `requirements.txt` 把 Python/torch 版本写成两套冲突范围；本机 Python 3.14.7 并不能证明 FLUX、FramePack 或 ACE-Step 依赖在同一解释器可安装。
- FramePack 官方 Windows 压缩包是其文档明确给出的 Windows 路线；若需各引擎独立 venv，FramePack 源码路线仍须在 Windows 单独重现安装。
- ACE-Step 官方 README 提供 Windows venv 安装步骤，但要求安装 PyTorch CUDA wheel；当前项目不能照搬其依赖进入主应用环境。
- Diffusers 的 FLUX API 可以采用标准 PyTorch/Diffusers API；先建立已锁定的 Python + PyTorch Windows wheel 清单，再做最小导入和单图实测。
- `xformers`/Flash Attention 不是本项目先决条件；涉及本地编译、CUDA kernel 或 Windows 轮子时按可选优化单独评估。

## 目标机测试验收模板（尚未执行）

每个模型至少执行：冷启动、连续两次任务、取消、OOM/错误恢复、重启后离线推理。保存以下记录：

| 字段 | 记录内容 |
|---|---|
| 主机/系统 | Windows build、GPU 型号、驱动、RAM、目标磁盘剩余空间 |
| 软件栈 | Python、PyTorch、CUDA runtime、推理库和扩展精确版本 |
| 模型 | repo、revision/SHA、license、所有模型文件大小/hash |
| 请求 | 任务类型、输入分辨率、输出宽高/帧数/时长、精度、量化、采样参数 |
| 资源 | 起始 VRAM、峰值 VRAM、结束 VRAM、峰值 RAM、运行时间 |
| 结果 | 输出文件路径、格式/尺寸/时长、可打开或播放检查、日志和退出状态 |
| 离线 | 下载缓存后物理断网/阻断外连，确认应用启动、引擎启动和真实推理均成功且无外部请求 |

## 官方来源

- [Diffusers FLUX pipeline 与优化文档](https://huggingface.co/docs/diffusers/main/en/api/pipelines/flux)
- [FramePack 官方硬件/安装说明](https://github.com/lllyasviel/FramePack/blob/main/README.md)；[Windows release](https://github.com/lllyasviel/FramePack/releases/tag/windows)
- [HunyuanVideo 官方 README（requirements、显存及 FP8）](https://github.com/Tencent-Hunyuan/HunyuanVideo/blob/main/README.md)
- [ACE-Step 官方 README](https://github.com/ace-step/ACE-Step/blob/main/README.md)
- [PyTorch 官方本地安装选择器](https://pytorch.org/get-started/locally/)

## 结果

当前能确认机器有 RTX 3060 12GB，但不能确认任何候选模型已兼容。Phase 3 的成功标准必须是本机生成真实文件、记录资源数据并完成离线复测；在此之前 UI 状态应为“未验证”。
