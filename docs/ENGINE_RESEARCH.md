# 辰鹄 AI 创作中心：引擎调研

**状态：Phase 0 桌面资料调研完成；未在 RTX 3060 上运行任何模型。**  
**核查日期：2026-10-11**。仓库主分支是可变引用；官方仓库的精确 commit SHA 和模型文件字节数未能从当前网络环境取得，部署前必须重新记录并锁定。

## 结论摘要

| 模态 | 优先候选 | 桌面证据 | 许可证/适配结论 | 决策 |
|---|---|---|---|---|
| 图片 | FLUX.1-schnell + Diffusers | BFL 官方代码列出 Apache-2.0 权重；Diffusers 有本地 pipeline 和 CPU offload 示例 | 适合作为首个候选；RTX 3060 的峰值显存、速度、完整 Windows 依赖组合仍未实测 | 推荐进入用户确认后的 Phase 1/3 准备 |
| 视频 | FramePack（I2V 优先） | 官方 README 明列 Windows、RTX 30/40/50、最低 6GB；官方 Windows 包为 CUDA 12.6 / PyTorch 2.6，并称模型另需下载 30GB 以上 | 12GB 仅有项目方最低显存声明，不是本机实测；模型权重许可与稳定可编程控制接口待核验 | 有条件候选；确认许可和接口后再决定集成 |
| 音乐 | ACE-Step v1.5 | 官方项目页提供 Windows venv 安装说明和本地运行/API 路线，2026-01-28 公告 v1.5 | README 的 8GB 优化与 3090 基准属于旧版本/旧测试，不能外推至 v1.5；v1.5 权重许可和 RTX 3060 实测未核实 | 有条件候选；暂不集成 |

这不是硬件认证或法律意见。所有“兼容”在本项目中须以目标 Windows 机器、固定环境、目标模型和可打开的实际输出文件通过测试为准。

## 调研边界与记录规则

- 优先核查官方 GitHub、官方模型仓库、官方模型卡、官方许可证和官方框架文档。
- 代码和权重许可证分开记录。项目源代码的许可证不能代替模型权重的许可。
- 模型文件精确总大小、commit SHA、校验和均须在准备阶段从官方清单读取并写入部署清单；本次 API 请求超时，不能用估算替代。
- PyTorch 当前 Windows wheel、Python 3.14、CUDA wheel 与所选推理依赖须形成同一组可复现版本。显卡驱动显示的 CUDA UMD 版本不等于已经安装 CUDA Toolkit 或 PyTorch CUDA runtime。
- 离线能力指已缓存所有权重、配置、tokenizer、代码与依赖后，在网络断开状态运行；首次准备会联网。每个引擎都要实测阻断联网后能否启动和推理。

## 图片候选：FLUX.1-schnell + Hugging Face Diffusers

| 项目 | 核查结果 |
|---|---|
| 官方项目/仓库 | [BFL FLUX 代码仓库](https://github.com/black-forest-labs/flux)；[FLUX.1-schnell 权重](https://huggingface.co/black-forest-labs/FLUX.1-schnell)；[Diffusers](https://github.com/huggingface/diffusers) |
| 版本 | FLUX 权重仓库使用可变 `main`/模型仓库引用；精确 commit 未取得。Diffusers 最新核查到 v0.41.0（官方 release 页面）。实施时应锁定 SHA/tag，而不是 `main` 或宽松 `>=`。 |
| 代码许可证 | BFL 仓库包含推理代码；Diffusers 为 Apache-2.0。各自文件及依赖仍需纳入最终 notice 清单。 |
| 权重许可证 | BFL 模型目录明确将 FLUX.1 [schnell] 列为 Apache-2.0；不是 FLUX.1 [dev] 的非商用许可证。模型仓库中的全部配套组件仍要逐项检查。 |
| Windows / 依赖 | Diffusers 提供标准 Python pipeline 示例；Windows 上需用选定 PyTorch 官方 wheel 做组合安装测试。Diffusers 示例使用 PyTorch、`diffusers`，并可用 `enable_model_cpu_offload()` / `enable_sequential_cpu_offload()`；并未要求必须装 xformers。 |
| 显存 | Diffusers 文档对 FLUX 给出低显存 offload/量化方法，文中“4–32GB”是优化适用范围描述，不是对某块 GPU 的保证。官方同时说明 Flux 模型系统加载可达约 50GB RAM/VRAM，FP8 在 GPU/CUDA/PyTorch 组合上可能不稳定。12GB RTX 3060 的可运行性必须目标机实测。 |
| 体积/下载来源 | 官方模型仓库；精确文件总大小本次未取得。下载前从仓库文件清单计算所选 dtype/组件大小、检查磁盘，再由用户确认。 |
| 离线调用 | 使用 `FluxPipeline.from_pretrained(本地模型目录, local_files_only=True)`；应用启动和推理不得回退到 Hub 或自动联网下载。此为建议集成方式，尚未在项目中实现。 |
| 调用方式 | Diffusers `FluxPipeline`；官方文档给出 schnell 4 步、`guidance_scale=0` 的示例以及保存图片的调用方式。可由独立引擎进程封装。 |
| 风险/结论 | 目前仓库 requirements 混有 torch 2.1 与 `>=2.7` 两套声明，且当前 Python 为 3.14.7；不能据此推定能安装成功。首选候选，但禁止声称“FP8 后 6GB”或“已适配 RTX 3060”。 |

## 视频候选：FramePack

| 项目 | 核查结果 |
|---|---|
| 官方项目/仓库 | [FramePack 官方仓库](https://github.com/lllyasviel/FramePack)；官方 README 链接 [Windows release](https://github.com/lllyasviel/FramePack/releases/tag/windows)。 |
| 版本 | 可见的官方 Windows 包发布于 2025-04-18，CUDA 12.6 / PyTorch 2.6；README 更新日期/模型版本需安装前复核。GitHub 当前未提供常规语义版本 release。 |
| 代码许可证 | 仓库 `LICENSE` 为 Apache-2.0。 |
| 权重许可证 | FramePack 使用的模型权重在独立模型仓库发布。FramePack 源码许可不能证明这些权重为 Apache-2.0；须依据实际下载的模型仓库 license 与上游模型条款确认，本次未取得可审计的完整权重条款，状态为待核实。 |
| Windows / 依赖 | 官方列出 Windows/Linux，并提供 Windows 一键包；源码安装段落主要描述 Linux。Windows 包与自建独立 venv 是两条不同部署路径，后者要单独验证。 |
| 显存 | 官方 README 声称 RTX 30/40/50 系列、至少 6GB，并称 13B 模型可在 6GB 显存上生成长视频。这是项目方声明；不是 RTX 3060 桌面卡的本项目实测，也没有可用于本项目的完整峰值数据。 |
| 体积/下载来源 | 官方称模型会自动从 Hugging Face 下载“超过 30GB”；这不是精确清单。不得允许自动下载，必须改为用户确认后的准备步骤并记录各文件校验值。 |
| 离线/API | 官方桌面实现有生成界面及进度反馈。主仓库没有在本次核查中确认稳定的、可导入调用并支持查询/取消的公开 API 契约。集成需将推理执行封装到独立子进程/适配器，并证明可以报告真实进度、取消及收尾；不可要求用户打开其单独 UI。 |
| 支持任务 | 官方示例以图像条件视频生成为主，README 亦列出文本到视频示例。建议第一版仅在验证过的图生视频路径上提供参数。 |
| 结论 | 比 Open-Sora/SVD 的旧判断更有希望，进入有条件候选。许可证、模型准确来源、可控制的进程接口和本机实测都是集成阻塞条件。 |

### 视频排除/对照项

| 候选 | 官方依据 | 结论 |
|---|---|---|
| 原版 HunyuanVideo | [官方仓库](https://github.com/Tencent-Hunyuan/HunyuanVideo)写明 13B，Linux 测试；官方峰值表为 45GB（544×960×129 帧）/60GB（720p×129 帧），测试硬件为 80GB GPU。 | RTX 3060 12GB 不满足所列场景，排除其原始配置；FP8 节省约 10GB 仍不能据此推断能落入 12GB。权重受 Tencent Hunyuan Community License 约束。 |
| Open-Sora | [官方仓库](https://github.com/hpcaitech/Open-Sora)及各权重仓库须按具体版本审查。 | 现有旧文的 48GB 数字和伪造 API 示例没有可核验的官方出处；本报告不再用其作为候选推荐。12GB Windows 可行性未证实，暂不纳入。 |
| Stable Video Diffusion XT | [官方权重仓库](https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt)。 | 权重使用 Stability AI 模型许可而不是通用 Apache；本次未核实具体版本的显存/Windows组合，也不把网络估算的 14GB 当事实。暂不推荐。 |

## 音乐候选：ACE-Step v1.5

| 项目 | 核查结果 |
|---|---|
| 官方项目/仓库 | [ACE-Step 1.5 官方仓库](https://github.com/ace-step/ACE-Step-1.5)；[ACE-Step 1.5 项目页](https://ace-step.github.io/ace-step-v1.5.github.io/)；[模型权重](https://huggingface.co/ACE-Step/Ace-Step1.5)。旧 [ACE-Step 仓库](https://github.com/ace-step/ACE-Step) 是 v1 路线，不应代替 v1.5 资料。 |
| 版本 | 官方项目页链接论文 arXiv:2602.00744；代码 README 于 2026-04-02 记录 v1.5 XL 发布。精确 commit SHA/模型 revision 尚未取得。 |
| 代码许可证 | v1.5 独立 GitHub 仓库 README 声明 MIT；旧 v1 仓库 Apache-2.0 不适用于新仓库。 |
| 权重许可证 | Hugging Face v1.5 模型仓库 card/metadata 未能读取；不能从 MIT 代码许可证推定权重也是 MIT。权重许可证和商业条件仍待核实，确认前不分发、不声称商用许可。 |
| Windows / 依赖 | 新仓库推荐 Python 3.11–3.12、`uv sync`，并列出 Windows portable package。源码路线与 portable package 需分别审计依赖和联网行为。目标工作区 Python 3.11.9 符合版本范围，但尚未安装 ACE-Step 依赖。 |
| 显存 | v1.5 项目页宣称低于 4GB VRAM；官方 GPU 指南将 12–16GB 列为 Tier 5，推荐 1.7B LM、CPU offload 和 INT8，2B DiT 路线可考虑。XL 4B 需至少 12GB 且 offload+quantization，12–16GB 档只标为边缘可用；20GB 为建议值。上述为官方配置声明，不是本机实测。 |
| 体积/下载来源 | 官方 Hugging Face/ModelScope 模型目录；精确文件大小和 SHA 未取得，下载前须列明每个组件。默认启动会自动下载模型，集成必须关闭或拦截该行为。 |
| 离线/API | 新仓库提供本地 Python API、CLI 与 REST API；缺模型时默认启动有自动下载路径。须采用本地 checkpoint、离线设置、独立进程和网络阻断测试，禁止联网回退。 |
| 功能/限制 | v1.5 支持文本/歌词音乐生成、时长控制及音频编辑；UI 参数须按所选模型配置筛选，不照搬引擎 UI 全部选项。 |
| 结论 | 进入许可核查和后续实测候选队列；模型许可证仍是 blocker。 |

### 音乐排除/对照项

| 候选 | 官方依据 | 结论 |
|---|---|---|
| Meta MusicGen / AudioCraft | [官方 AudioCraft 仓库](https://github.com/facebookresearch/audiocraft)模型卡明确：代码 MIT，权重 CC-BY-NC 4.0。 | 权重非商用。若项目要支持商业创作，应排除；如果仅个人非商业用途仍须遵守署名及非商业条款。仓库依赖和 Windows 独立部署也需另测。 |
| Stable Audio Open | [官方模型仓库](https://huggingface.co/stabilityai/stable-audio-open-1.0)、[官方工具仓库](https://github.com/Stability-AI/stable-audio-tools)。 | 权重使用 Stability AI Community License，而非 Apache/MIT；条款有收入/用途等限制，且常规模型显存需求不能当成 12GB 适配保证。先不选。 |

## 推荐执行顺序（等待用户确认）

1. 先冻结 Windows 环境矩阵和应用基础架构，不下载模型。
2. 批准后，在独立 venv 中对 FLUX.1-schnell 做最小安装与离线流程准备；之后再由用户确认是否下载模型做目标机推理测试。
3. FramePack 和 ACE-Step v1.5 先完成直接权重许可证、文件清单、API/取消接口和依赖审查；逐项通过后再批准下载/实测。
4. 未通过实测的模块在 UI 中明确为“尚未安装/未验证”，不提供假生成。

## 官方证据来源

- [BFL FLUX 仓库及模型许可证目录](https://github.com/black-forest-labs/flux)；[FLUX schnell license 文件](https://github.com/black-forest-labs/flux/blob/main/model_licenses/LICENSE-FLUX1-schnell)；[FLUX dev license 文件](https://github.com/black-forest-labs/flux/blob/main/model_licenses/LICENSE-FLUX1-dev)
- [Diffusers FLUX pipeline 文档](https://huggingface.co/docs/diffusers/main/en/api/pipelines/flux)；[Diffusers 发布页](https://github.com/huggingface/diffusers/releases)
- [FramePack README](https://github.com/lllyasviel/FramePack/blob/main/README.md)；[FramePack LICENSE](https://github.com/lllyasviel/FramePack/blob/main/LICENSE)；[Windows release](https://github.com/lllyasviel/FramePack/releases/tag/windows)
- [HunyuanVideo README](https://github.com/Tencent-Hunyuan/HunyuanVideo/blob/main/README.md)；[HunyuanVideo LICENSE](https://github.com/Tencent-Hunyuan/HunyuanVideo/blob/main/LICENSE.txt)
- [ACE-Step 1.5 README](https://github.com/ace-step/ACE-Step-1.5/blob/main/README.md)；[ACE-Step 1.5 LICENSE](https://github.com/ace-step/ACE-Step-1.5/blob/main/LICENSE)；[ACE-Step 1.5 项目页](https://ace-step.github.io/ace-step-v1.5.github.io/)；[ACE-Step v1 README](https://github.com/ace-step/ACE-Step/blob/main/README.md)
- [AudioCraft MusicGen 模型卡](https://github.com/facebookresearch/audiocraft/blob/main/model_cards/MUSICGEN_MODEL_CARD.md)
- [PyTorch Windows 安装选择器](https://pytorch.org/get-started/locally/)
