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
            metadata={"test_only": True, "prompt_received": bool(request.prompt)},
            test_only=True,
        )

    async def shutdown(self):
        return None


def create_adapter():
    return IsolatedTestAdapter()