"""Typed task request and persistence records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskRequest:
    task_type: str
    engine_id: str
    prompt: str
    model_id: str | None = None
    input_files: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    output_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    test_only: bool = False


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    request: TaskRequest
    status: TaskStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    progress: float | None
    output_files: tuple[str, ...]
    error_message: str | None
    result_metadata: dict[str, Any]
    retry_of: str | None