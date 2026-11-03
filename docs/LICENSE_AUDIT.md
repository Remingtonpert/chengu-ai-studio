# 辰鹄 AI 创作中心：许可证审计

**核查日期：2026-10-11**  
**范围：Phase 0 候选推理代码、模型权重及当前依赖声明。不是法律意见，也不是完整 SBOM。**

## 核心结论

- FLUX.1-schnell 与 FLUX.1-dev 的权重许可证不同：schnell 为 Apache-2.0，dev 为 FLUX.1 [dev] Non-Commercial License。旧文中“FLUX dev 可商用”的结论错误，现已撤回。
- FramePack 代码为 Apache-2.0，但不能据此认定所用模型权重可按 Apache-2.0 使用；具体权重来源及 license 未核实，阻止集成和分发。
- ACE-Step 仓库代码为 Apache-2.0；v1.5 实际权重的模型仓库条款尚未从官方模型卡确认，阻止启用/分发及任何商业使用结论。
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
| ACE-Step v1.5 | 独立官方仓库 ACE-Step-1.5 README 声明 MIT；旧 v1 仓库 Apache-2.0 不适用于新代码 | v1.5 HF 权重仓库 license 未核实 | 未核实前不推断商业权利、再分发权或衍生权 | 有条件候选，权重许可仍阻止启用 |
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
| **图片** | FLUX.1-dev | FLY-1.1* | ⚠️ 待确认 | ✅ | 中 |
| **图片** | SDXL | Stable Diffusion | ⚠️ 需检查 | ✅ | 中 |
| **视频** | Open-Sora (代码) | Apache-2.0 | ✅ | ✅ | 低 |
| **视频** | Open-Sora (模型) | 待审计 | 🔴 未知 | ⚠️ 可能 | 高 |
| **视频** | SVD | Stability AI* | ⚠️ 待确认 | ✅ | 中 |
| **音频** | - | - | - | - | 🔴 无合格方案 |

*\*表示需要进一步审查的许可证*

---

## 七、Phase 0 交付

✅ `docs/LICENE_AUDIT.md` (本文件)

### 下一步行动

1. 等待用户确认调研结论
2. 如果同意，继续 Phase 1 开发
3. 同时开始编写 `HARDWARE_COMPATIBILITY.md`, `ARCHITECTURE.md`, `DEVELOPMENT_PLAN.md`
