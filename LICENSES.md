# 辰鹄AI创作中心 · 模型许可台账（LICENSES.md）

**更新日期：2026-11-05。** 收口原则：本项目集成的**全部模型许可只有 Apache-2.0 与 MIT 两种**；
FLUX.1-dev、FLUX.1-Kontext-dev 等非商用（FLY-1.1 等）许可模型**一律不接入、不在 UI 出现、不进安装包**。

下表逐项列明：模型名 / 用途 / 来源 / 许可 / 可否商用 / 可否随包再分发。许可字段经
Hugging Face 仓库 API（hf-mirror 镜像）实测读取；大小为本机实测（直连 huggingface.co 在本机超时）。

| 模型 / 组件 | 用途 | 来源 | 本机路径与实测大小 | 许可 | 商用 | 随安装包再分发 |
|---|---|---|---|---|---|---|
| FLUX.1-schnell | 文生图 / 图生图（唯一图像引擎） | Hugging Face `black-forest-labs/FLUX.1-schnell` | `models\flux-schnell`，23 文件 / 33.73 GB，逐文件 SHA-256 校验 | **Apache-2.0** | ✅ | ✅ |
| Wan2.1-T2V-1.3B-Diffusers | 文生视频（引擎 `wan-t2v`） | Hugging Face `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` | `models\wan2.1`（HF 缓存快照，与 VACE 共享 blobs；两仓库合计物理 **44.18 GB**） | **Apache-2.0** | ✅ | ✅ |
| Wan2.1-VACE-1.3B-diffusers | 图生视频·首帧条件生成（引擎 `wan-i2v`） | Hugging Face `Wan-AI/Wan2.1-VACE-1.3B-diffusers` | 同上（同一 `models\wan2.1` 缓存根） | **Apache-2.0** | ✅ | ✅ |
| ACE-Step 1.5 代码 | AI 音乐引擎代码 | GitHub `ace-step/ACE-Step-1.5` | `vendor\ACE-Step-1.5`（仓库快照） | **MIT** | ✅ | ✅ |
| ACE-Step 1.5 权重 | AI 音乐 | Hugging Face `ACE-Step/Ace-Step1.5` | `models\ace-step-1.5`，4 个 safetensors / 9.40 GB，逐文件校验 | **MIT** | ✅ | ✅ |
| Qwen3-Embedding-0.6B | 音乐管线文本嵌入组件 | Hugging Face `Qwen/Qwen3-Embedding-0.6B` | 随音乐权重目录 | **Apache-2.0** | ✅ | ✅ |

## 再分发义务与说明

- **Apache-2.0**：再分发时随目录保留 LICENSE 文本与 NOTICE/版权声明。FLUX.1-schnell 与
  Wan2.1 两个 HF 快照目录内均已含各自 LICENSE 文件，robocopy 打包原样携带，满足归属义务。
- **MIT**：随包保留 LICENSE 文本即可（ACE-Step 仓库与权重仓库均含）。
- Wan2.1 模型卡对生成内容的合规提示（不得用于伪造真人影像等）与许可条款相互独立，本项目
  界面在视频产物与文档中如实保留该提示。
- **明确不集成**：FLUX.1-dev / FLUX.1-Kontext-dev（FLY-1.1 non-commercial）、原 Anima 引用的
  `circlestone-labs/Anima`（非商业许可）及一切台账外模型——不下载、不注册、不在任何界面出现。
  `app\engine\image\flux.py` 支持表中的 `flux-dev` 为历史死条目，永不接线、永不注册、UI 不可达。
- **第三方 Python 依赖（venv 内 torch / diffusers / transformers / PySide6 等）**：许可逐包清点
  与 `THIRD_PARTY_NOTICES.md`（SBOM）属正式发布前必做项，见 `docs\LICENSE_AUDIT.md`；本机自用与
  评测演示阶段不受影响（PySide6 为 LGPL，随包分发桌面端时需按条款提供可替换说明，正式对外
  发布前完成）。
- 详细审计过程与证据链见 `docs\LICENSE_AUDIT.md`。
