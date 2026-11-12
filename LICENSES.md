# 辰鹄AI创作中心 · 模型许可台账（LICENSES.md）

**更新日期：2026-11-10。** 收口原则：本项目**注册在岗**的模型许可只有 **Apache-2.0、MIT、Gemma Terms of Use**
三类，**全部允许商用与随包再分发**（Gemma 条款额外附禁止用途清单，再分发时要按条款传递）；
FLUX.1-dev、FLUX.1-Kontext-dev 等非商用（FLY-1.1 等）许可模型**一律不接入、不在 UI 出现、不进安装包**。
唯一悬着的组件是 Sana 用的 DC-AE 32× VAE，其上游仓库的许可标注**待复核**（见第一节与 `docs\LICENSE_AUDIT.md` 文末）。

> **2026-11-10 变更（文生图再次换型）**：文生图改由 **Sana-1.6B 多语言版**（引擎 `sana-multiling`，
> `Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers`，Apache-2.0，模型卡原文声明支持
> Emoji / 中文 / 英文与任意混合提示词）承担，新增权重 `models\sana-multiling`（磁盘实测 **9.06 GB**，
> 只取 fp16 变体）。上一版（2026-11-09）的 **`wan-t2i`（Wan2.1-T2V-1.3B 单帧出图）同日下架**：
> 它的分辨率受视频档 480p 像素预算限制，拿不到 1024 级，属结构性天花板；**图生图保留 `wan-i2i`**
> （VACE-1.3B `reference_images=` 参考重绘，仍复用视频已下载的同一套 Wan2.1 权重，零新增下载、同一 Apache-2.0）。
> 再往前（2026-11-08）的 SD1.5 三件套由此**继续停留在注册表之外**：引擎不注册、UI 不出现，权重与引擎代码
> 留在磁盘作后备（见第二节「未注册残留」）；它退场的理由是**选型**——CLIP 没有中日韩词表，中文只能靠关键词桥接，
> 不满足「必须原生支持中英文」，**不是许可问题**。FLUX 方案（2026-11-08 之前）因权重 23 GB、FP8 仍需 12 GB 以上
> 显存、且上游已转 gated 而作废，权重与脚本已删除。

下表逐项列明：模型名 / 用途 / 来源 / 许可 / 可否商用 / 可否随包再分发。许可字段经
Hugging Face 仓库 API（hf-mirror 镜像）实测读取；大小为本机实测（直连 huggingface.co 在本机超时）。

## 一、注册在岗（会被引擎加载、会进 UI）

| 模型 / 组件 | 用途 | 来源 | 本机路径与实测大小 | 许可 | 商用 | 随安装包再分发 |
|---|---|---|---|---|---|---|
| Sana-1.6B 多语言版 | **文生图**（引擎 `sana-multiling`，`.venv-image` 独立 worker） | Hugging Face `Efficient-Large-Model/Sana_1600M_1024px_MultiLing_diffusers` | `models\sana-multiling`（HF 缓存快照），磁盘实测 **9.06 GB**（fp16 三件：text_encoder 4757.0+229.5 MB、transformer 3060.3 MB、vae 1191.2 MB） | **Apache-2.0**（`gated:false`；快照内含 11358 字节 LICENSE 文件） | ✅ | ✅ |
| Gemma-2-2B-IT 文本编码器 | Sana 的提示词编码（原生中文 / 英文 / 中英混合 / Emoji） | 内置在 Sana 仓库 `text_encoder/`，无需登录 Google 的受限仓库 | 含在上面 9.06 GB 内（最大单件 **4.99 GB**，也是「逐模块换入」策略的由来） | **Gemma Terms of Use**（允许商用，附禁止用途清单） | ✅ | ✅（随包须按条款附文本，属发布前必做） |
| DC-AE f32c32 VAE | Sana 的 32× 自编码器（宽高必须是 32 的倍数的原因） | 快照内 `vae/config.json` 的 `_name_or_path` 指向 `mit-han-lab/dc-ae-f32c32-sana-1.0-diffusers` | 1.19 GB（随 Sana 快照） | **待复核**：随 Sana 仓库以 Apache-2.0 快照形式在盘上，但上游仓库自身的许可标注尚未核验 | ⚠️ 待复核 | ⚠️ 复核前不单独再分发该组件 |
| Wan2.1-T2V-1.3B-Diffusers | 文生视频（引擎 `wan-t2v`）。（旧「文生图 `wan-t2i`＝同权重 `num_frames=1` 单帧出图」已于 **2026-11-10 下架**：分辨率受视频档 480p 像素预算限制，已由 Sana 取代；历史任务记录仍可读取） | Hugging Face `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` | `models\wan2.1`（HF 缓存快照 `0fad780a…`，与 VACE 共享 blobs；两仓库合计物理 **44.18 GB**） | **Apache-2.0** | ✅ | ✅ |
| Wan2.1-VACE-1.3B-diffusers | 图生视频·首帧条件（引擎 `wan-i2v`）**＋图生图**（引擎 `wan-i2i`，`reference_images=` 参考重绘单帧） | Hugging Face `Wan-AI/Wan2.1-VACE-1.3B-diffusers` | 同一 `models\wan2.1` 缓存根（快照 `ec4d2cb0…`） | **Apache-2.0** | ✅ | ✅ |
| UMT5-XXL 文本编码器 | Wan 引擎（文生视频 / 图生视频 / 图生图）的中英文提示词编码 | 随 Wan2.1 快照（`text_encoder/`，多语言原生支持中英文） | 随上两行同一缓存 | **Apache-2.0**（随 Wan 仓库） | ✅ | ✅ |
| ACE-Step 1.5 代码 | AI 音乐引擎代码（引擎 `ace-step-15`） | GitHub `ace-step/ACE-Step-1.5` | `vendor\ACE-Step-1.5`（仓库快照） | **MIT** | ✅ | ✅ |
| ACE-Step 1.5 权重 | AI 音乐（中英文歌词 / 纯器乐） | Hugging Face `ACE-Step/Ace-Step1.5` | `models\ace-step-1.5`，4 个 safetensors / **9.40 GB**，逐文件校验 | **MIT** | ✅ | ✅ |
| Qwen3-Embedding-0.6B | 音乐管线文本嵌入组件 | Hugging Face `Qwen/Qwen3-Embedding-0.6B` | 随音乐权重目录 | **Apache-2.0** | ✅ | ✅ |
| Qwen3-8B（Q4_K_M GGUF） | AI 智能体对话（本地 llama.cpp，不用 Ollama） | Hugging Face `Qwen/Qwen3-8B` 的 GGUF 量化 | `models\llm\gguf\Qwen3-8B-Q4_K_M.gguf`，实测 **4.68 GB** | **Apache-2.0** | ✅ | ✅ |
| llama.cpp 运行时 | 智能体推理引擎（`llama-server` 子进程） | `ggml-org/llama.cpp` | `tools\llama_cpp`（二进制 + 源码，内含 LICENSE 文本） | **MIT** | ✅ | ✅ |

## 二、未注册残留（磁盘上仍在，但引擎不注册、UI 不出现；后备件，正式发布前再决定是否删除）

2026-11-09 从注册表退下、2026-11-10 复核确认不再启用的 SD1.5 三件套与引擎代码（`app\engine\image\sd15.py`、`app\tasks\sd_worker.py`、`app\engine\image\hires.py`、`app\engine\prompt_bilingual.py`）。
许可本身仍是商用允许的四类之内，退场的理由是**选型**（SD1.5 的 CLIP 文本编码器没有中日韩词表，中文只能靠关键词桥接，
不满足「必须原生支持中英文」这条硬要求）而不是许可问题。留盘的理由：它是本项目**历史上唯一真机实测过显存峰值的出图路径**，
要自行验证仍可手工 `register_factory`，届时要按下面的 OpenRAIL 义务执行。

| 模型 / 组件 | 曾用途 | 来源 | 本机路径与实测大小 | 许可 |
|---|---|---|---|---|
| RealVis V5.1（SD1.5 精调） | 文生图 / 图生图（旧引擎 `sd-realvis` / `sd-realvis-i2i`） | Hugging Face `SG161222/Realistic_Vision_V5.1_noVAE` | `models\sd-realvis`，快照 `1e9f017a…`，实测 **3.97 GB** | OpenRAIL-M（可商用，须随包传递使用限制条款） |
| SD1.5 VAE（ft-MSE） | RealVis（noVAE 版）必需编解码组件 | Hugging Face `stabilityai/sd-vae-ft-mse` | `models\sd-vae-ft-mse`，快照 `31f26fde…`，实测 **0.31 GB** | MIT |
| LCM-LoRA（SD1.5） | 4-8 步加速 LoRA | Hugging Face `latent-consistency/lcm-lora-sdv1-5` | `models\lcm-lora-sd15`，快照 `cf2fced5…`，实测 **0.13 GB** | OpenRAIL++ |

## 再分发义务与说明

- **Apache-2.0**：再分发时随目录保留 LICENSE 文本与 NOTICE/版权声明。Wan2.1 两个 HF 快照目录内均已含各自 LICENSE 文件，
  robocopy 打包原样携带，满足归属义务。Qwen3 GGUF 量化文件旁边没有模型卡/LICENSE 文本，随包再分发前需把 Qwen3 仓库的
  LICENSE 全文一并放进 `models\llm\gguf\`（已列入 `docs\LICENSE_AUDIT.md` 的未完成项，联网后一次补齐即可，不影响本机运行）。
- **MIT**：随包保留 LICENSE 文本即可（ACE-Step 仓库与权重仓库均含；llama.cpp 目录内含）。
- **Gemma Terms of Use**（Sana 的文本编码器）：允许商用与再分发，但条款里带**禁止用途清单**，并要求再分发时把条款
  文本一并传递、下游不得移除。权重已内置在 Sana 仓库（因此本项目不需要 Google 账号或受限仓库访问），
  但随安装包分发时仍要把 Gemma 条款文本放进 `models\sana-multiling` 旁边或 `THIRD_PARTY_NOTICES.md` 清单，
  这一项与 Qwen3 GGUF 缺模型卡文本属同一批发布前必做项（见 `docs\LICENSE_AUDIT.md`）。
- **DC-AE f32c32 VAE（Sana 的 `vae/`）**：上游仓库 `mit-han-lab/dc-ae-f32c32-sana-1.0-diffusers` 的许可标注**尚未核验**，
  本会话未能联网打开该仓库。在此之前，文档、界面与安装包清单都不得把它写成「已确认 Apache/MIT」；
  复核前不单独特异性再分发该组件（它只随 Sana 快照整体在盘上）。
- Wan2.1 模型卡对生成内容的合规提示（不得用于伪造真人影像等）与许可条款相互独立，本项目
  界面在图片与视频产物说明及文档中如实保留该提示（图生图与视频同一条约束）。
- **明确不集成**：FLUX.1-dev / FLUX.1-Kontext-dev（FLY-1.1 non-commercial）、FLUX.1-schnell（上游 hf-mirror 已转
  gated repo，403 实测 8 连败，方案作废）、原 Anima 引用的 `circlestone-labs/Anima`（非商业许可）、
  Meta `facebook/musicgen-*` 全系列（实测 cc-by-nc-4.0 非商用）、Real-ESRGAN 超分（官方权重标注仅供非商业使用）
  及一切台账外模型——不下载、不注册、不在任何界面出现。
- **2026-11-10 文生图选型中因许可否决的候选**：Kolors（README 原文：商用需填问卷申请）、HunyuanDiT
  （HF 元数据 `license:other`，条件授权）、Janus-Pro-1B（README 声明受 DeepSeek Model License 约束）——
  一律不接入。Chroma1-HD（42.19 GB、基于 FLUX.1-schnell）与 CogView4-6B（许可其实合格：Apache-2.0、中英双语）
  是因**体积/显存超 6GB 铁律**出局，不是许可问题。取证与全过程见 `docs\ENGINE_RESEARCH.md`
  「2026-11-10 文生图再次选型」一节。
- **第三方 Python 依赖（venv 内 torch / diffusers / transformers / PySide6 等）**：许可逐包清点
  与 `THIRD_PARTY_NOTICES.md`（SBOM）属正式发布前必做项，见 `docs\LICENSE_AUDIT.md`；本机自用与
  评测演示阶段不受影响（PySide6 为 LGPL，随包分发桌面端时需按条款提供可替换说明，正式对外
  发布前完成）。
- 若日后重新启用第二节里的 OpenRAIL-M / OpenRAIL++ 组件：允许商用、允许再分发，但两条硬性要求——
  ① 分发时必须把许可全文与「使用限制」（不得用于违法犯罪、人身伤害、监控追踪、伪造身份等）一并传递；
  ② 下游使用不得移除这些限制。上游 RealVis 快照里只在模型卡（README）声明许可、没有 LICENSE 文件，
  届时再分发须把 OpenRAIL-M / OpenRAIL++ 全文随包附上。

- 详细审计过程与证据链见 `docs\LICENSE_AUDIT.md`。
