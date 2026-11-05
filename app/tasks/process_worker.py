"""JSON-lines subprocess host for trusted engine adapter factories."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import sys
from collections.abc import Callable
from typing import Any

from app.tasks.models import TaskRequest, TaskResult


def _load_factory(reference: str) -> Callable[[], Any]:
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("adapter factory must be written as module:callable")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute_name)
    if not callable(factory):
        raise TypeError("adapter factory is not callable")
    return factory


def _request_from_json(payload: dict[str, Any]) -> TaskRequest:
    return TaskRequest(
        task_type=payload["task_type"],
        engine_id=payload["engine_id"],
        model_id=payload.get("model_id"),
        prompt=payload["prompt"],
        input_files=tuple(payload.get("input_files", ())),
        parameters=payload.get("parameters", {}),
    )


def _result_to_json(result: TaskResult) -> dict[str, object]:
    return {
        "output_files": list(result.output_files),
        "metadata": result.metadata,
        "test_only": result.test_only,
    }


async def _serve(factory_reference: str) -> None:
    adapter = _load_factory(factory_reference)()
    methods = {"health_check", "validate_request", "generate", "shutdown"}
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        request_id: object = None
        should_exit = False
        try:
            message = json.loads(line)
            request_id = message["id"]
            method = message["method"]
            if method not in methods:
                raise ValueError(f"unsupported engine method: {method}")
            payload = message.get("payload")
            if method == "health_check":
                result = await adapter.health_check()
            elif method == "validate_request":
                outcome = adapter.validate_request(_request_from_json(payload))
                if inspect.isawaitable(outcome):
                    await outcome
                result = None
            elif method == "generate":
                generation_result = await adapter.generate(_request_from_json(payload))
                if not isinstance(generation_result, TaskResult):
                    raise TypeError("engine generate must return TaskResult")
                result = _result_to_json(generation_result)
            else:
                await adapter.shutdown()
                result = None
                should_exit = True
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:2000],
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if should_exit:
            return


def main() -> int:
    # JSON-lines 协议必须全程 UTF-8：stdout 为管道时 Windows 默认用系统代码页
    # （GBK/cp936），adapter 端按 UTF-8 解码会失败并报"无效 IPC 数据"。
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-factory", required=True)
    args = parser.parse_args()
    asyncio.run(_serve(args.adapter_factory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())