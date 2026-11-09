"""JSON-lines subprocess host for trusted engine adapter factories."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from app.core.process_guard import start_parent_watchdog
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
        # ensure_ascii=True：中文报错原文也走 \uXXXX 转义，回传方向同样不受管道代码页影响。
        # 响应写出必须永远成功进入 try 保护范围：若 json.dumps 因 result 里的不可
        # 序列化对象抛错，异常会逃出 _serve，worker 进程直接退出而这一帧响应根本没写
        # 出去，adapter 只能干等 request_timeout（当前是 30 分钟）才判失败，
        # 错误原文也全部丢失。所以先单独 try 序列化，失败就退化成结构化错误帧。
        try:
            encoded = json.dumps(response, ensure_ascii=True)
        except Exception as exc:  # noqa: BLE001 - 序列化失败也要回结构化错误帧
            fallback = {
                "id": response.get("id"),
                "ok": False,
                "error": f"{type(exc).__name__}: 响应序列化失败: {exc}"[:2000],
            }
            encoded = json.dumps(fallback, ensure_ascii=True)
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()
        if should_exit:
            return
        # 协作式轮换：引擎适配器可以在处理完请求后置 should_recycle=True，
        # 要求 IPC 宿主在**响应已经写出之后**优雅退出（音乐引擎用它规避上游
        # #1338 的连续生成崩溃）。退出时机由本循环掌控，绝不会在下一个请求的
        # 生成过程中被计时器杀掉。
        if getattr(adapter, "should_recycle", False):
            return


def main() -> int:
    # JSON-lines 协议必须全程 UTF-8：三条流都要显式指定，否则中文 Windows 上
    # 管道默认走系统代码页（gbk/cp936），且 gbk 解码带 errors="surrogateescape"，
    # 会把按 UTF-8 写入的非 ASCII 文本解成带**孤立代理码位**（\udcXX）的坏字符串：
    #   - 写方向：worker 按 gbk 写 stdout → adapter 按 UTF-8 解码失败 → "无效 IPC 数据"；
    #   - 读方向（本次根因）：adapter 按 UTF-8 字节写入 stdin，worker 的 sys.stdin
    #     仍按 gbk+surrogateescape 解码 → prompt 里混入 \udcXX → 该 str 无法转成
    #     Rust String → tokenizers 报 "TextEncodeInput must be Union[TextInputSequence,
    #     Tuple[InputSequence, InputSequence]]"。纯 ASCII 两种代码页解出的字节完全一致，
    #     所以英文提示词永远正常、中文提示词永远失败——与任务库里 46 条记录的分布完全吻合。
    # 读方向用 strict（不用 surrogateescape）：万一帧不是合法 UTF-8，宁可让子进程
    # 明确报错退出，也绝不把损坏的文本静默喂给下游模型。
    sys.stdin.reconfigure(encoding="utf-8", newline="\n", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-factory", required=True)
    args = parser.parse_args()
    # 护栏第二层：父进程（跑 TaskService 的那个客户端）一没，本 worker 立刻自我结束。
    # 正在生成时它卡在 adapter.generate 里，读不到 stdin 关闭的 EOF，也没人来得及调
    # shutdown()，就会一直占着显存不放——只有它自己盯着父进程才靠得住。
    parent_raw = str(os.environ.get("CHENGUAI_PARENT_PID") or "").strip()
    if parent_raw.isdigit():
        start_parent_watchdog(int(parent_raw))
    asyncio.run(_serve(args.adapter_factory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())