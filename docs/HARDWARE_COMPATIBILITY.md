# 辰鹄 AI 创作中心：硬件兼容性评估
> **2026-11-10 更新（读前必看）**：文生图引擎现职为 **Sana-1.6B 多语言版**（引擎 id `sana-multiling`，磁盘 9.06 GB，跑在 `.venv-image`），图生图为 **Wan2.1-VACE-1.3B**（引擎 id `wan-i2i`，与视频共用 `.venv-video` 与 `models\wan2.1`）。历史上的 FLUX.1-schnell（2026-11-08 因显存铁律整套删除，含权重与专用脚本）与 SD1.5 精调 RealVis（`sd-realvis` / `sd-realvis-i2i`，磁盘 4.41 GB，真机峰值 1.88-2.09 GB / 后继路径 4359 MB，**现已不注册、界面上看不到**）**都不是现役能力**；本文中把它们当作现役的段落属于**历史记录**。选型结论以 `docs\ENGINE_RESEARCH.md` 文末「2026-11-10 文生图再次选型」为准，许可以 `LICENSES.md` 为准。三条铁律：**可商用 / 中英文提示词 / 显存峰值 ≤6GB**。
> 
> **Sana 这一档的显存数字目前只是推算，不是实测**：fp16 三件按逐模块 CPU 换入，任一时刻显存里只有当前一件，最大单件是 4.99 GB 的文本编码器，因此预期峰值压在 6 GB 内；本机显存长期被常驻大模型占满，真机验收（`scripts\acceptance_image.bat`）尚未跑成。每次生成会把 `peak_vram_mb` 写进产物元数据，验收逐档核对，**不伪造、不预设结论**。

**核查日期：2026-10-11（调研快照）；显存/磁盘相关条目已于 2026-11-05、2026-11-08、2026-11-10 分批复核**  
**状态：当时候选模型均未执行真实推理；此前提已过期——2026-11 起 FLUX.1-schnell 曾在本机 RTX 3060 12GB 完成真实推理并产出图片（512/1024/1216×704 各档，显存/耗时如实记录；该模型已于 2026-11-08 整套删除，这些记录属历史），SD1.5 路径四项（文生图 / 真 1080p / 图生图 / 文生视频）真机峰值 1884-2094 MB 与 2021 MB 已实测并留证（SD1.5 部分自 2026-11-09 起不再注册，Wan 视频仍在岗），ACE-Step 1.5 权重校验就位、端到端验收排队中；Anima 未推理（已于 2026-11-05 整体移除）。资料声明不等于本项目兼容认证；当前实测数据见根 `README.md`。**

## 当前开发机观测

| 项目 | 实际观测 | 说明 |
|---|---|---|
| 操作系统 | Windows；用户目标为 Windows 11 64 位 | 本次命令未收集 Windows edition/build |
| Python | 系统 3.14.7；项目四套 venv 已建成（2026-11-05 起，`.venv-image` 于 2026-11-10 复核版本）：`.venv` 3.11.9（PySide6 主进程，零 torch）、`.venv-image`（**Sana 文生图 worker**：torch 2.14.1+cu130、diffusers 0.41.0、transformers 5.19.0）、`.venv-video`（Wan2.1 文生视频 / 图生视频 / **图生图** worker）、`.venv-music`（transformers<4.58，与 image 环境硬冲突故隔离） | 推理只发生在后三个 venv 的子进程；主进程零 torch。引擎注册对失效的 venv 配置会告警并回退默认环境（`.venv-image\Scripts\python.exe`、`models\sana-multiling`） |
| GPU | NVIDIA GeForce RTX 3060，WDDM | 设备可见 |
| 显存 | 12,288 MiB 总量；命令执行时约 2,501 MiB 已占用（2026-10-11 观测，此后未重测） | 空闲量会随桌面应用变化；模型预算不得假设全部 12GB 可用。历史实测留证：FLUX 出图（worker 权重加载约 31-34GB RAM，模型已删除）、SD1.5 路径 1884-2094 MB、Wan 视频 2021 MB。现役 Sana 走逐模块 CPU 换入，按最大单件 4.99 GB + 激活**推算**峰值压在 6GB 内、**尚未真机实测**；引擎自带两道显存闸门——空闲 <5120 MB 自动退到逐层换入（慢约两三倍）、<1500 MB 直接拒绝并点名占用者，另有一道加载闸门要求空闲物理内存 ≥16 GB。若本机有其他常驻引擎占用显存，先跑 `scripts\release_local_engine.bat` 释放，验收脚本也会自等待而非硬撞 |
| Driver/CUDA 信息 | NVIDIA-SMI 显示 Driver 617.14、CUDA UMD 13.4 | 这是驱动报告，不代表已安装对应 CUDA Toolkit，也不能决定 PyTorch wheel；应使用 PyTorch 官方安装矩阵 |
| RAM/磁盘 | 64 GB RAM（2026-11-05 复核）；输出盘 `D:\ChenguAI\Output` | `models\` 实测合计 **71.73 GB**：注册在岗的引擎权重 `sana-multiling` 9.06 + `wan2.1` 44.18 + `ace-step-1.5` 9.40 + `llm` 4.68 = 67.32 GB；未注册的 SD1.5 后备三件 4.41 GB（`sd-realvis` 3.97 + `sd-vae-ft-mse` 0.31 + `lcm-lora-sd15` 0.13）。FLUX 快照 33.73 GB 已于 2026-11-08 删除（释放磁盘 31.41 GB）；ACE-Step `.cache` 残片约 3.67 GB 已于 2026-11-05 删除。Sana 新增权重下载约 9.3 GB（只取 fp16 变体，黑名单跳过 fp32 三件约 12.3 GB） |

## 兼容性判定规则

- 12GB 是设备标称总显存，不是应用可用预算。Windows 桌面和其他 GPU 进程会占用显存；应记录模型加载前、峰值和结束后的可用/已用显存。
- 仅当官方记录了目标任务设置或目标机实测时才填写显存数字。权重参数量乘精度大小不是峰值显存；激活、缓存、编码器和加载副本均会增加用量。
- CPU offload、量化和低分辨率属于有条件的优化路线，不是兼容性证明。每次报告应包含权重 revision、dtype/量化方法、分辨率、帧数/时长、步数、峰值 VRAM、RAM、耗时、结果文件和日志。
- 所有 GPU 生成任务默认串行；至少预留桌面及显示所需显存。开始任务前若安全余量不足，应排队/拒绝并解释，不要直接尝试加载。
- Python、PyTorch、CUDA runtime、NVIDIA 驱动和每个扩展（如 Triton/xformers/Flash Attention）必须作为一个组合验证。优先使用 PyTorch 官方 Windows wheel；不在系统 Python 上修改全局依赖。

## 候选硬件证据与结论

| 候选 | 官方硬件说明 | RTX 3060 12GB 结论 |
|---|---|---|
| Sana-1.6B 多语言版（引擎 `sana-multiling`，**现役文生图**） | 官方模型卡声明 1024px 档，多语言版原文「supports Emoji, Chinese and English and all mixed prompts」；diffusers 有官方 `SanaPipeline` 与 model/sequential CPU offload 路线。权重磁盘实测 9.06 GB | **尚未真机实测**（本机显存被常驻大模型占满）。策略：fp16 三件（编码器 4757.0+229.5 MB＝最大单件 4.99 GB、transformer 3060.3 MB、DC-AE VAE 1191.2 MB）逐模块换入，任一时刻显存里只有当前一件 → 按最大单件 + 激活**推算** <6 GB。两道显存闸门：空闲 <5120 MB 自动退逐层换入（慢约两三倍）、<1500 MB 拒绝并点名占用者；加载闸门要求空闲物理内存 ≥16 GB。尺寸：32 的倍数、单边 ≤2048、单遍像素预算 1.10 MP、目标硬上限 4.20 MP；超预算走「按预算出底图 + Lanczos 放大」（元数据 `upscaled=true`、`tiles=0`）。验收：`scripts\acceptance_image.bat`（要求空闲显存 ≥6144 MB 且空闲内存 ≥16000 MB，两档 1024×1024 / 1920×1088 逐档打印实测峰值，超 6000 MB 立即停止） |
| （历史，已删除）FLUX.1-schnell + Diffusers | Diffusers 官方 FLUX 文档提供 sequential/model CPU offload 和量化示例；说明 Flux 大型组件完整加载约 50GB，低 VRAM 优化示例不是目标机基准；FP8 可能受 GPU/CUDA/PyTorch 组合影响。 | （历史记录）**曾实测通过（2026-11）**：RTX 3060 12GB 上 512×512、1024 档、1216×704 均产出真实图片，`HF_HUB_OFFLINE=1` 离线复现成功，显存/耗时如实记录（根 `README.md`）。但即使如此也守住不了「峰值 ≤6GB」：23.7 GB 权重 FP8 仍需 12 GB 以上，与常驻程序共存必然爆显存，故 2026-11-08 整套删除；上游仓库随后转 gated（403）。 |
| FramePack | 官方 README 声明支持 NVIDIA RTX 30XX 且至少 6GB；宣称 13B 模型在最低 6GB 设备上工作，较慢显卡速度下降。官方 Windows release 使用 CUDA 12.6/PyTorch 2.6；模型下载超过 30GB。 | 未采用。视频引擎 2026-11-05 收口为 **Wan2.1 1.3B**（T2V + VACE，Apache-2.0）：diffusers 官方管线、fp32 VAE + bf16 + model_cpu_offload，官方 480p 档 81 帧、16fps，官方宣称 8GB+ 可跑（bf16/offload），12GB 档预计可跑 480p（真实耗时/显存峰值以本机实测为准，验收排队中）；原 Anima 引用已整体移除（非商业许可）。 |
| 原始 HunyuanVideo | 官方主仓库列出 544×960×129 帧 45GB、720p×129 帧 60GB 峰值，并说明在 80GB GPU 测试、测试系统 Linux。FP8 约节省 10GB 的官方表述不能直接代表总需求降至 12GB。 | 排除原始推荐配置。FramePack 是独立低显存候选，不可把其项目声明归因于 Hunyuan 原生推理。 |
| Wan2.1 1.3B + VACE-1.3B（引擎 `wan-t2v` / `wan-i2v` / `wan-i2i`，**在岗**） | 官方 480p 档 81 帧、16fps；官方宣称 8GB+ 可跑（bf16/offload）；权重两快照合计物理 44.18 GB，文本编码器 UMT5-XXL 单件约 11 GB，加载需 16-18 GB 空闲物理内存 | **文生视频已实测（2026-11-08）：512×384、49 帧、12 步、逐层卸载，显存峰值 2021 MB / 378.7 s**，远低于 6 GB；加载阶段走 `app\engine\memprobe.py` 的 16384 MB 空闲物理内存闸门（2026-11-08 那次被系统静默杀掉是直接起因）。**图生图 `wan-i2i`**（2026-11-09 起）同一套权重、同一 venv：480p 档、16 的倍数、像素预算 459264（约 0.46 MP，= `budget.MAX_PIXELS`，三端同一份数），单帧 token 量约为 81 帧视频的 1/13，显存特性与已实测的视频档同级；逐层卸载同样要求约 16 GB 空闲内存。旧「文生图 `wan-t2i`」已于 2026-11-10 下架（受视频档像素预算限制） |
| ACE-Step v1.5 | 官方仓库测试表包含 RTX 4090、A100、RTX 3090、M2 Max；未见 RTX 3060 v1.5 实测。README 的 8GB 优化公告在 v1.5 发布前。 | 环境已建成：`.venv-music`（transformers<4.58）+ 权重 10.03GB 校验 + 12GB 档必需的 DiT offload 环境变量（上游 #1337）；提交门槛与代码同一份账（`app/tasks/music_worker.py` 的 `MUSIC_MIN_VRAM_MB = 6144`，两端前端 6144 总显存 / 6144 空闲）——原先文档写的 12288/7000 是早期 12GB 卡口径，代码降到 6GB 档后没同步，已按代码改正。**端到端出音频的真机验收仍排队等显存窗口，通过前不标记「已验证」。** |
| MusicGen | 官方 MusicGen 模型卡提供 300M/1.5B/3.3B 规格，未以此证明 Windows 12GB 组合。 | 硬件可能性不是主要阻塞；权重 CC-BY-NC 4.0 的非商用条款是商业路线的阻塞项。 |

## Windows / 依赖现状

- 当前 `requirements.txt` 把 Python/torch 版本写成两套冲突范围；本机 Python 3.14.7 并不能证明 FLUX、FramePack 或 ACE-Step 依赖在同一解释器可安装。
- FramePack 官方 Windows 压缩包是其文档明确给出的 Windows 路线；若需各引擎独立 venv，FramePack 源码路线仍须在 Windows 单独重现安装。
- ACE-Step 官方 README 提供 Windows venv 安装步骤，但要求安装 PyTorch CUDA wheel；当前项目不能照搬其依赖进入主应用环境。
- （历史）Diffusers 的 FLUX API 曾按标准 PyTorch/Diffusers API 接入，模型已删除。现役文生图走 Diffusers 官方 `SanaPipeline`，`.venv-image` 的版本已锁定（torch 2.14.1+cu130、diffusers 0.41.0、transformers 5.19.0）；「最小导入 + 单图实测 + 断网复测」这一整套属验收排队项，通过前不标记已验证。
- `xformers`/Flash Attention 不是本项目先决条件；涉及本地编译、CUDA kernel 或 Windows 轮子时按可选优化单独评估。

## 目标机测试验收模板（部分模型已按此模板执行，见上表「RTX 3060 12GB 结论」列的实测/未实测标注）

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

- [Sana 多语言版模型卡（1024px，`Sana_1600M_1024px_MultiLing_diffusers`）](https://huggingface.co/Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers)；[Efficient-Scaled-DiT 项目页](https://nvlabs.github.io/Sana/)
- [Diffusers `SanaPipeline` 文档](https://huggingface.co/docs/diffusers/main/en/api/pipelines/sana)
- [Wan2.1-T2V-1.3B-Diffusers](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers)；[Wan2.1-VACE-1.3B-diffusers](https://huggingface.co/Wan-AI/Wan2.1-VACE-1.3B-diffusers)
- [Diffusers FLUX pipeline 与优化文档](https://huggingface.co/docs/diffusers/main/en/api/pipelines/flux)（历史候选，模型已删除）
- [FramePack 官方硬件/安装说明](https://github.com/lllyasviel/FramePack/blob/main/README.md)；[Windows release](https://github.com/lllyasviel/FramePack/releases/tag/windows)
- [HunyuanVideo 官方 README（requirements、显存及 FP8）](https://github.com/Tencent-Hunyuan/HunyuanVideo/blob/main/README.md)
- [ACE-Step 官方 README](https://github.com/ace-step/ACE-Step/blob/main/README.md)
- [PyTorch 官方本地安装选择器](https://pytorch.org/get-started/locally/)

## 结果

机器有 RTX 3060 12GB 这一条早已确认；能不能跑则由本机实测说了算，Phase 3 的成功标准一直是「本机生成真实文件 + 记录资源数据 + 断网复测」，在此之前 UI 状态诚实显示「未验证」。按这条标准，当前的确认程度是：**Wan 视频在岗且实测过峰值（2021 MB）**；SD1.5 路径的四项峰值属历史记录、模型已不再注册；**ACE-Step 1.5 环境就位但端到端出音频未验收**；**Sana 文生图是「策略与闸门已就位、峰值按体积推算 <6GB 但真机未实测」**——这一格必须等 `scripts\acceptance_image.bat` 跑通并逐档打印 `peak_vram_mb` 后才能改成「已验证」，在那之前任何文档与界面都不得写成实测结论。