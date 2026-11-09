"""Test-only adapter loaded exclusively by the subprocess IPC test."""

from app.tasks.models import TaskRequest, TaskResult


class IsolatedTestAdapter:
    engine_id = "test-process-only"
    task_types = frozenset({"image"})
    test_only = True

    async def health_check(self):
        return True, "isolated test worker; no media generation"

    async def validate_request(self, request: TaskRequest):
        if request.engine_id != self.engine_id:
            raise ValueError("engine id mismatch")

    async def generate(self, request: TaskRequest):
        return TaskResult(
            metadata={
                "test_only": True,
                "prompt_received": bool(request.prompt),
                # 原样回显收到的提示词：用于断言跨进程 JSON-lines 传输没有把
                # 非 ASCII 文本解码坏（编码错乱会混入孤立代理码位）。
                "prompt_text": request.prompt,
                "prompt_type": type(request.prompt).__name__,
                "prompt_has_lone_surrogate": any(
                    0xD800 <= ord(ch) <= 0xDFFF for ch in (request.prompt or "")
                ),
            },
            test_only=True,
        )

    async def shutdown(self):
        return None


def create_adapter():
    return IsolatedTestAdapter()


class RecyclingTestAdapter(IsolatedTestAdapter):
    """每次生成后请求「本轮响应写完后优雅退出」的适配器。

    复刻音乐引擎（app/tasks/music_worker.py）达到 max_tasks 阈值后设置的
    should_recycle 标记，用于验证退出时机由 IPC 宿主掌控、不会在下一个任务的
    生成过程中把进程杀掉。
    """

    def __init__(self):
        self.generations = 0
        self.should_recycle = False

    async def generate(self, request: TaskRequest):
        self.generations += 1
        result = await super().generate(request)
        result.metadata["worker_generations_served"] = self.generations
        self.should_recycle = True
        return result


def create_recycling_adapter():
    return RecyclingTestAdapter()