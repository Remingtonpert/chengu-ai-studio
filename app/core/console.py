"""控制台输出编码兜底（两端共用）。

Windows 中文环境的默认代码页是 GBK，print 中文或 emoji 会抛 UnicodeEncodeError，
用户看到的不是「哪里错了」而是「程序一启动就崩」。这里统一把 stdout/stderr 切到
UTF-8（errors="replace"），并把这件事做成幂等、失败也不影响主流程的一行调用。
引擎 worker 子进程另有 process_worker 里的 stdio 重配置，两者互不影响。
"""

from __future__ import annotations

import sys


def ensure_utf8_console() -> None:
    """把 stdout/stderr 切到 UTF-8。幂等；任何异常都吞掉，不掩盖真正的启动错误。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 流可能已被重定向或关闭
            pass
