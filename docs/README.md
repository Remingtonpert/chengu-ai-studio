# 文档索引
> **2026-11-08 更新（读前必看）**：图像引擎已从 FLUX.1-schnell 换成 **SD1.5 精调 RealVis V5.1 + sd-vae-ft-mse + LCM-LoRA**（引擎 id `sd-realvis` / `sd-realvis-i2i`，磁盘 4.41 GB，真机显存峰值 1.88-2.09 GB）；FLUX 的权重、引擎与脚本**已全部删除**。本文中把 FLUX 当作现役模型的段落属于**历史记录**；许可与选型结论以 `LICENSES.md`、`docs\LICENSE_AUDIT.md` 文末「2026-11-08 增补」和 `README.md` 文末「FLUX 出局」一节为准。三条铁律：**可商用 / 中英文提示词 / 显存峰值 ≤6GB**。

状态（2026-11-05）：Phase 1 桌面骨架与 Phase 2 任务生命周期已通过测试验收；Phase 3 真实引擎已接入——FLUX.1-schnell 已在本机 RTX 3060 离线产出真实图片（英文批次成功；中文端到端验收脚本就位、等待显存窗口执行），ACE-Step 1.5 权重逐文件校验通过（端到端验收排队中），Anima 视频权重待用户决策。当前进度真相源是根 `README.md`；模块与接口权威清单见 [MODULES.md](MODULES.md)。

## 调研

- [引擎调研报告](ENGINE_RESEARCH.md)：图片、视频、音乐候选和官方证据。
- [许可证审计](LICENSE_AUDIT.md)：引擎代码与模型权重分别审查，并列出待核实风险。
- [硬件兼容性](HARDWARE_COMPATIBILITY.md)：当前机器实测信息与官方硬件声明分离。

## 规划与现状

- [架构提案](ARCHITECTURE.md)：目标桌面架构、模块边界和现状差距（含 Phase 3 实际落地记录）。
- [开发计划](DEVELOPMENT_PLAN.md)：阶段验收、阻断项和用户确认门槛。

文档中的项目方最低显存声明不是辰鹄软件的兼容认证；除 FLUX 已留证的真实推理外，其余能力在通过本机推理与离线复测前一律标记「未验证」，也不在许可未核实前下载/分发模型。