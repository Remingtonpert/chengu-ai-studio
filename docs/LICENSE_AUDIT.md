# 辰鹄 AI 创作中心：许可证审计

**核查日期：2026-10-11（调研快照）· 2026-11-05 增补：FLUX.1-schnell（Apache-2.0）与 ACE-Step 1.5（官方仓库 README 声明 MIT，权重经 `ACE-Step/Ace-Step1.5` 下载并逐文件校验）已实际集成进本地引擎；同日经 Hugging Face 仓库 API 复核权重仓库 license 元数据（见文末「模型许可台账」），两者均可商用、可随安装包再分发。同日视频选型收口：`Wan-AI/Wan2.1-T2V-1.3B-Diffusers`（文生视频）与 `Wan-AI/Wan2.1-VACE-1.3B-diffusers`（图生视频·首帧条件）经 HF API 实测均 **Apache-2.0（gated:false）**，已下载入包（`models/wan2.1`，两快照合计物理 44.18 GB）。Anima 视频引用已于 2026-11-05 整体移除（其真实上游为不可商用的社区许可，且代码原引用的仓库不存在），不下载、不入包。**  
**范围：Phase 0 候选推理代码、模型权重及当前依赖声明。不是法律意见，也不是完整 SBOM。**

## 核心结论

- FLUX.1-schnell 与 FLUX.1-dev 的权重许可证不同：schnell 为 Apache-2.0，dev 为 FLUX.1 [dev] Non-Commercial License。旧文中“FLUX dev 可商用”的结论错误，现已撤回。
- FramePack 代码为 Apache-2.0，但不能据此认定所用模型权重可按 Apache-2.0 使用；具体权重来源及 license 未核实，阻止集成和分发。
- ACE-Step 仓库代码为 Apache-2.0；v1.5 实际权重的模型仓库条款尚未从官方模型卡确认，阻止启用/分发及任何商业使用结论。（2026-11-05 修订：`ACE-Step/Ace-Step1.5` 权重仓库 license 元数据经 HF 仓库 API 复核为 **MIT**，上述阻止结论解除，见文末台账。）
- MusicGen 代码 MIT、权重 CC-BY-NC 4.0；权重明确为非商业许可。
- 本地推理/离线运行不等于所有许可证允许商业使用，也不等于可以再分发模型权重。

## 候选许可证矩阵

| 组件 | 代码许可 | 权重许可 | 商业/分发风险 | Phase 0 结论 |
|---|---|---|---|---|
| Hugging Face Diffusers | Apache-2.0 | 不适用，推理库本身不授予模型权利 | 依赖组件逐包清点；Apache notice/版权声明按条款保留 | 可作为候选推理库 |
| FLUX.1-schnell | BFL 推理代码许可需按所用源文件核对；Diffusers 代码 Apache-2.0 | BFL 仓库明确为 Apache-2.0 | 保留 Apache-2.0 文本和模型归属；其他配套权重逐项审核 | 首选图片模型候选 |
| FLUX.1-dev / dev 派生模型 | BFL 推理代码有独立许可，Diffusers 本身仍为 Apache-2.0 | FLUX.1 [dev] Non-Commercial License v1.1.1 | 仅授权非商业、非生产用途；商业用途须另行取得 BFL 授权；附有使用和内容过滤义务 | 不作为本项目默认模型；勿与 schnell 混淆 |
| FramePack | 官方仓库 Apache-2.0 | FramePack 权重/上游权重需在实际模型 repo 分别确认；当前待核实 | 不得仅凭代码许可部署；须核对上游条款、使用区域、商业门槛及 NOTICE 义务 | 权重条款通过前不集成、不随应用分发 |
| Tencent HunyuanVideo（对照） | 官方代码许可文件为 Tencent Hunyuan Community License Agreement 相关授权 | 同一社区协议覆盖其明确定义的模型权重和相关作品 | 有地域、用途、署名/Powered by、分发 Notice 和特定商业门槛条款；先阅读完整现行文本 | 原生大显存路线排除；FramePack 衍生/依赖关系需律师或授权方确认 |
| ACE-Step v1.5 | 独立官方仓库 ACE-Step-1.5 README 声明 MIT；旧 v1 仓库 Apache-2.0 不适用于新代码 | v1.5 HF 权重仓库 license 元数据经 HF API 复核为 MIT（2026-11-05） | 保留 MIT 文本即可商用与再分发；再分发时随包保留 LICENSE | 已启用并随便携安装包分发 |
| Meta MusicGen / AudioCraft | MIT | CC-BY-NC 4.0 | 非商业；不得作为商业创作默认模型；需履行 CC BY-NC 条款 | 商业路线排除 |
| Stable Audio Open | `stable-audio-tools` 代码仓库许可证按仓库核查 | Stability AI Community License（具体模型/当前条款需保存副本） | 条款依主体、收入和用途而异，非无条件商业许可 | 暂不选为默认方案 |

## 当前项目依赖声明风险

当前 `requirements.txt` 同时包含重复依赖约束和互相冲突的 PyTorch 声明（包括固定的 2.1 系列以及 `>=2.7`）；还存在未锁定版本的范围。它不是可复现的依赖锁文件，也不能据它生成准确的第三方许可证清单。不得按此文件直接进行正式部署。

在 Phase 1 依赖选择前，需按每个实际 Python distribution 记录：包名、精确版本、项目 URL、许可证文件/表达式、传递依赖、wheel 来源和 CUDA 构建标识；导出安装环境的 SBOM/NOTICE。`xformers`、Gradio 等可选组件不得在未确定用途前作为强制依赖。

## 离线及网络行为审计要求

离线可用性由应用实现和实测决定，不是许可证属性。正式设计必须做到：

- 联网准备必须由用户主动触发，显示来源、目标路径、精确字节数、许可证与磁盘检查结果；默认不自动下载。
- 下载完成后锁定文件 hash、revision、组件清单；推理设置 `local_files_only` 或对应的本地模式，网络断开后运行。
- 主程序和引擎不得调用云端推理 API、远程登录、遥测或自动公开分享。若模型引擎本身会请求远端，必须禁用并通过网络阻断实测。
- 用户输入、媒体、日志不得发送到外部服务。Ollama 仅为可选本机文本助手，不能承担三种生成任务。

## 官方来源

- [BFL FLUX 仓库模型许可证表](https://github.com/black-forest-labs/flux)；[schnell Apache-2.0 原文](https://github.com/black-forest-labs/flux/blob/main/model_licenses/LICENSE-FLUX1-schnell)；[dev 非商用原文](https://github.com/black-forest-labs/flux/blob/main/model_licenses/LICENSE-FLUX1-dev)
- [Diffusers LICENSE](https://github.com/huggingface/diffusers/blob/main/LICENSE)
- [FramePack LICENSE](https://github.com/lllyasviel/FramePack/blob/main/LICENSE)
- [Tencent HunyuanVideo LICENSE.txt](https://github.com/Tencent-Hunyuan/HunyuanVideo/blob/main/LICENSE.txt) 与 [Notice](https://github.com/Tencent-Hunyuan/HunyuanVideo/blob/main/Notice)
- [ACE-Step 1.5 README](https://github.com/ace-step/ACE-Step-1.5/blob/main/README.md)；[ACE-Step 1.5 LICENSE](https://github.com/ace-step/ACE-Step-1.5/blob/main/LICENSE)；[v1.5 权重仓库](https://huggingface.co/ACE-Step/Ace-Step1.5)；[旧 v1 仓库](https://github.com/ace-step/ACE-Step)
- [AudioCraft LICENSE](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE)；[MusicGen 权重许可模型卡](https://github.com/facebookresearch/audiocraft/blob/main/model_cards/MUSICGEN_MODEL_CARD.md)
- [Stable Audio Open 模型仓库](https://huggingface.co/stabilityai/stable-audio-open-1.0)；[稳定音频工具代码仓库](https://github.com/Stability-AI/stable-audio-tools)

## 未完成的合规工作

1. 获取实际选择的每个权重仓库完整 license、model card、revision 和文件清单。
2. 逐项审查文本编码器、VAE、tokenizer、量化 checkpoint、LoRA 和随应用分发的其他模型。
3. 按最终安装锁文件生成软件 SBOM、模型清单及 `THIRD_PARTY_NOTICES.md`。
4. 审核商业用途、再分发和目标地区限制；需要时由专业法律顾问确认。

## 2026-11-05 集成后增补

- FLUX.1-schnell：权重快照 23 文件 / 33.73 GB 已逐文件 SHA-256 校验并锁定，仅本机推理使用，不随仓库分发（`models/` 在 `.gitignore`）；Apache-2.0 归属文本随快照保留。
- ACE-Step 1.5：官方代码仓库声明 MIT；四文件权重已逐文件校验（`.gitignore` 排除，不随 git 仓库分发；随便携安装包分发）。HF 权重仓库 license 元数据已于 2026-11-05 经 HF 仓库 API 复核为 **MIT**，商用/再分发结论按台账执行。下载残留 `.cache` 目录（约 3.67GB，全部为 `.incomplete` 残片与 metadata，无 snapshots）已于 2026-11-05 删除，删除后四个 safetensors 头部结构与张量清单复核完好。
- Anima 视频：已于 2026-11-05 **整体移除**（代码、权重引用、许可标注、安装包条目全部删除）。移除依据：① 代码原引用的 `moovenio/Anima_2B_model` 与 `mit-han-lab/dc-anime-tiny` 仓库经 HF/镜像 API 查询均不存在（404）；② 本机 diffusers 0.41.0 无 `AnimaPipeline` 符号；③ 真实上游 `circlestone-labs/Anima` 为**文生图**模型且许可证为 `circlestone-labs-non-commercial-license`（非商业），不符合本项目可商用要求。因此不下载、不入包；视频模态在界面诚实显示「暂无可用本地视频引擎 · 调研中」，不提交任务、不伪造输出。
- 依赖清点：主 `.venv`（PySide6）、`.venv-image`（torch cu130/diffusers/transformers 5.x）、`.venv-music`（transformers<4.58）三环境已实际建立；正式 SBOM 与 `THIRD_PARTY_NOTICES.md` 仍未生成，属发布前必做项（见上节 3）。
- 本节以下旧草稿残留（一张无出处的半成品许可矩阵表与「Phase 0 交付」清单）已于 2026-11-05 清理：该表把 FLUX.1-dev 标为「待确认」、含 SDXL/Open-Sora/SVD 等早已排除的候选且无官方出处，与上文正式矩阵冲突，不再保留。审计以本文件上文矩阵与增补为准。

## 模型许可台账（2026-11-05，随便携安装包）

许可字段当日经 Hugging Face 仓库 API（经 hf-mirror 镜像）实测读取；直连 huggingface.co 在本机超时。台账外的一律不进安装包。

| 模型/组件 | 用途 | 来源仓库 | 大小（本机实测） | 许可（API 实测） | 商用 | 入便携包 |
|---|---|---|---|---|---|---|
| FLUX.1-schnell | 文生图 / 图生图（唯一图像引擎） | `black-forest-labs/FLUX.1-schnell` | 23 文件 / 33.73 GB，逐文件 SHA-256 校验 | Apache-2.0（gated:auto，经镜像取已校验快照） | ✅ | ✅ `models/flux-schnell` |
| ACE-Step 1.5 代码 | AI 音乐引擎 | GitHub `ace-step/ACE-Step-1.5`（`vendor/ACE-Step-1.5`） | 仓库快照 | MIT | ✅ | ✅ |
| ACE-Step 1.5 权重 | AI 音乐 | `ACE-Step/Ace-Step1.5` | 4 个 safetensors，9.40 GB（清理 `.cache` 残片后），逐文件校验 | MIT（gated:false） | ✅ | ✅ `models/ace-step-1.5` |
| Qwen3-Embedding-0.6B | 音乐管线文本嵌入组件 | `Qwen/Qwen3-Embedding-0.6B` | 随音乐权重目录 | Apache-2.0 | ✅ | ✅ |
| Wan2.1-T2V-1.3B-Diffusers | 文生视频（引擎 `wan-t2v`） | `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` | HF 快照下载至 `models/wan2.1`（transformer 5.29GB fp32 + text_encoder 21.16GB fp32 + vae 0.48GB，bf16 换入峰值约 10.6GB） | Apache-2.0（gated:false） | ✅ | ✅ |
| Wan2.1-VACE-1.3B-diffusers | 图生视频·首帧条件（引擎 `wan-i2v`） | `Wan-AI/Wan2.1-VACE-1.3B-diffusers` | 同一缓存根（text_encoder 10.58GB，VAE 与 T2V 共享 blob；两仓库合计物理 44.18 GB） | Apache-2.0（gated:false） | ✅ | ✅ 同上 |
| ~~Anima（已移除）~~ | ~~视频~~ | `circlestone-labs/Anima`（真实上游） | 不下载 | `circlestone-labs-non-commercial-license`（**非商业**） | ❌ | ❌ 不入包 |

- FLUX.1-schnell 快照内含 Apache-2.0 LICENSE 文本，再分发时随目录保留，满足 Apache 归属义务。
- 安装包内四个 venv（`.venv`/`.venv-image`/`.venv-video`/`.venv-music`）与 runtime 的第三方 Python 包许可仍未逐包清点：正式 SBOM 与 `THIRD_PARTY_NOTICES.md` 属发布前必做项（见上节），本机自用与评测演示不受影响。
