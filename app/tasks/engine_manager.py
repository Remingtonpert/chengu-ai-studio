"""Lazy adapter lifecycle management with failure containment."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.tasks.models import TaskRequest, TaskResult


class EngineAdapter(Protocol):
    engine_id: str
    task_types: frozenset[str]
    test_only: bool

    async def health_check(self) -> tuple[bool, str]: ...

    async def validate_request(self, request: TaskRequest) -> None: ...

    async def generate(self, request: TaskRequest) -> TaskResult: ...

    async def shutdown(self) -> None: ...


@dataclass(frozen=True)
class EngineStatus:
    engine_id: str
    state: str
    message: str


class EngineManager:
    """Creates adapters lazily and restarts them without restarting the UI."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], EngineAdapter]] = {}
        self._adapters: dict[str, EngineAdapter] = {}
        self._statuses: dict[str, EngineStatus] = {}
        self._lock = asyncio.Lock()

    def register_factory(
        self,
        engine_id: str,
        factory: Callable[[], EngineAdapter],
    ) -> None:
        if not engine_id.strip():
            raise ValueError("engine_id 不能为空")
        if engine_id in self._factories:
            raise ValueError(f"引擎已注册：{engine_id}")
        self._factories[engine_id] = factory
        self._statuses[engine_id] = EngineStatus(engine_id, "stopped", "尚未启动")

    def has_engine(self, engine_id: str) -> bool:
        return engine_id in self._factories

    def list_statuses(self) -> tuple[EngineStatus, ...]:
        return tuple(self._statuses.values())

    async def start_engine(self, engine_id: str) -> EngineAdapter:
        async with self._lock:
            adapter = self._adapters.get(engine_id)
            if adapter is not None:
                return adapter
            factory = self._factories.get(engine_id)
            if factory is None:
                raise KeyError(f"未注册引擎：{engine_id}")
            adapter = None
            try:
                adapter = factory()
                if adapter.engine_id != engine_id:
                    raise ValueError("adapter.engine_id 与注册 ID 不一致")
                healthy, message = await adapter.health_check()
                if not healthy:
                    raise RuntimeError(message or "引擎健康检查失败")
            except Exception as exc:
                cleanup_error = None
                if adapter is not None:
                    try:
                        await adapter.shutdown()
                    except Exception as shutdown_exc:
                        cleanup_error = shutdown_exc
                failure_message = str(exc)
                if cleanup_error is not None:
                    failure_message += f"; shutdown failed: {cleanup_error}"
                self._statuses[engine_id] = EngineStatus(
                    engine_id, "failed", failure_message[:1000]
                )
                raise
            self._adapters[engine_id] = adapter
            self._statuses[engine_id] = EngineStatus(engine_id, "ready", message)
            return adapter

    def get_adapter(self, engine_id: str) -> EngineAdapter:
        try:
            return self._adapters[engine_id]
        except KeyError as exc:
            raise RuntimeError(f"引擎尚未启动：{engine_id}") from exc

    def mark_failed(self, engine_id: str, message: str) -> None:
        self._statuses[engine_id] = EngineStatus(
            engine_id, "failed", message[:1000]
        )

    async def stop_engine(self, engine_id: str) -> None:
        async with self._lock:
            adapter = self._adapters.pop(engine_id, None)
            if adapter is not None:
                try:
                    await adapter.shutdown()
                except Exception as exc:
                    self._statuses[engine_id] = EngineStatus(
                        engine_id, "failed", f"shutdown failed: {exc}"[:1000]
                    )
                    raise
                previous_status = self._statuses.get(engine_id)
                if previous_status is None or previous_status.state != "failed":
                    self._statuses[engine_id] = EngineStatus(
                        engine_id, "stopped", "引擎已停止"
                    )

    async def release_idle_engines(self, keep_engine_id: str | None) -> tuple[str, ...]:
        """释放除 keep_engine_id 之外所有**还持有 worker 子进程**的引擎。

        keep_engine_id 传 None＝一个都不留：空闲十分钟的收割循环与「显存不够先让路」
        这两处用它，把本进程所有还挂着的引擎 worker 全停掉、显存当场交还。
        生成队列严格串行（执行者锁保证同一时刻只有一个任务在跑），所以其它引擎
        的 worker 只可能是上一个任务留下的「热缓存」。单张 12GB 卡上图像引擎常驻
        约 7GB 显存：不释放的话 ACE-Step 的 health_check 会因为空闲显存不足
        6000MB 直接拒绝启动，Wan2.1 更是 CUDA OOM——五类功能各自能跑、换着跑就
        跑不动。测试适配器没有 process_id，不会被这里动到。
        """
        released: list[str] = []
        for engine_id in tuple(self._adapters):
            if engine_id == keep_engine_id:
                continue
            adapter = self._adapters.get(engine_id)
            if adapter is None or getattr(adapter, "process_id", None) is None:
                continue
            try:
                await self.stop_engine(engine_id)
            except Exception:  # noqa: BLE001 - 释放失败不否决本次任务
                continue
            released.append(engine_id)
        return tuple(released)

    async def restart_engine(self, engine_id: str) -> EngineAdapter:
        await self.stop_engine(engine_id)
        return await self.start_engine(engine_id)

    async def shutdown(self) -> None:
        for engine_id in tuple(self._adapters):
            await self.stop_engine(engine_id)