"""视频生成引擎包（图生视频 / 文生视频）。

本包只在 .venv-image（含 torch / diffusers）中被导入；Web 主进程不加载它。
真实生成通过 app.tasks.video_worker.VideoTaskAdapter 接入任务系统。
"""
