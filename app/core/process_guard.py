"""进程护栏：谁拉起的进程谁负责收场，退出和崩溃都不留占显存的孤儿。

这里只有三种本事，全部只依赖标准库（ctypes / threading / os），便携包里不需要
任何额外组件：

1. :func:`assign_pid_to_kill_job` —— 把子进程挂进一个「句柄一关就杀光」的 Windows
   作业对象。本进程哪怕是被任务管理器 / ``Stop-Process`` 强行结束，内核也会替我们
   把它拉起的 worker 与 llama-server 一起收掉，显存当场归还。
2. :func:`start_parent_watchdog` —— 在**子进程自己**里跑一个守护线程：父进程一没，
   立刻自我结束。生成途中的 worker 卡在 ``generate`` 里，读不到 stdin 关闭的 EOF，
   只有这条能兜住它。
3. :func:`orphan_pids_by_image` —— 按镜像名找出「父进程已经没了」的遗留进程，
   下次启动时清掉。**只认镜像名**，别人的程序（哪怕也叫同一个名字但父进程还在）
   一律不动。

非 Windows 平台全部退化成空操作，调用方不用判平台。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from typing import Any

# OpenProcess 需要的最小权限：查存活用 QUERY_LIMITED，挂作业用 SET_QUOTA|TERMINATE。
_PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
_PROCESS_SET_QUOTA = 0x00000100
_PROCESS_TERMINATE = 0x00000001
_STILL_ACTIVE = 259

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_SNAPSHOT_HANDLE = ctypes.c_void_p(-1).value

_kernel32_lock = threading.Lock()
_kernel32_singleton: Any = None


def _kernel32() -> Any:
    """kernel32 单例（顺带把返回句柄的类型定成指针宽度，64 位下不会被截断）。"""
    global _kernel32_singleton
    if _kernel32_singleton is not None:
        return _kernel32_singleton
    with _kernel32_lock:
        if _kernel32_singleton is None:
            api = ctypes.windll.kernel32  # type: ignore[attr-defined]
            api.OpenProcess.restype = ctypes.c_void_p
            api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
            api.CloseHandle.argtypes = [ctypes.c_void_p]
            api.CreateJobObjectW.restype = ctypes.c_void_p
            api.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            api.SetInformationJobObject.restype = ctypes.c_bool
            api.SetInformationJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_size_t,
            ]
            api.AssignProcessToJobObject.restype = ctypes.c_bool
            api.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            api.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
            api.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
            _kernel32_singleton = api
    return _kernel32_singleton


def process_alive(pid: int) -> bool:
    """这个 PID 现在还在跑吗（查退出码，不去 Wait，也不打扰它）。"""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError) as exc:  # pragma: no cover - 非 Windows
            return isinstance(exc, PermissionError)
        except OSError:  # pragma: no cover - 非 Windows
            return False
        return True
    api = _kernel32()
    handle = api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not api.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return False
        return int(code.value) == _STILL_ACTIVE
    finally:
        api.CloseHandle(ctypes.c_void_p(handle))


def parent_pid_of(pid: int) -> int:
    """从进程快照里读这个 PID 的父 PID；读不到返回 0。"""
    if pid <= 0 or os.name != "nt":  # pragma: no cover - 非 Windows
        return 0
    api = _kernel32()

    class _Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeName", ctypes.c_wchar * 260),
        ]

    snapshot = api.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or int(snapshot) == _INVALID_SNAPSHOT_HANDLE:
        return 0
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        # Process32FirstW / Process32NextW 的第二个参数是指向结构体的指针。
        first = ctypes.windll.kernel32.Process32FirstW  # type: ignore[attr-defined]
        nxt = ctypes.windll.kernel32.Process32NextW  # type: ignore[attr-defined]
        if not first(ctypes.c_void_p(snapshot), ctypes.byref(entry)):
            return 0
        target = int(pid)
        while True:
            if int(entry.th32ProcessID) == target:
                return int(entry.th32ParentProcessID)
            if not nxt(ctypes.c_void_p(snapshot), ctypes.byref(entry)):
                return 0
    finally:
        api.CloseHandle(ctypes.c_void_p(snapshot))


def orphan_pids_by_image(image_name: str) -> tuple[int, ...]:
    """找出镜像名相同、但**父进程已经不在了**的进程（＝上一代客户端留下的孤儿）。

    父进程还活着的实例不动：那可能是另一个还在运行的客户端、或者用户自己在
    终端里手动跑的服务（``scripts\\run_agent_llm.bat``），都不是我们的孤儿。
    """
    if os.name != "nt":  # pragma: no cover - 非 Windows
        return ()
    wanted = str(image_name or "").strip().lower()
    if not wanted:
        return ()
    api = _kernel32()

    class _Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeName", ctypes.c_wchar * 260),
        ]

    snapshot = api.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or int(snapshot) == _INVALID_SNAPSHOT_HANDLE:
        return ()
    found: list[int] = []
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        first = ctypes.windll.kernel32.Process32FirstW  # type: ignore[attr-defined]
        nxt = ctypes.windll.kernel32.Process32NextW  # type: ignore[attr-defined]
        if not first(ctypes.c_void_p(snapshot), ctypes.byref(entry)):
            return ()
        while True:
            name = (entry.szExeName or "").lower()
            pid = int(entry.th32ProcessID)
            if name == wanted and pid != os.getpid() and not process_alive(int(entry.th32ParentProcessID)):
                found.append(pid)
            if not nxt(ctypes.c_void_p(snapshot), ctypes.byref(entry)):
                break
    finally:
        api.CloseHandle(ctypes.c_void_p(snapshot))
    return tuple(found)


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_size_t),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_size_t),
        ("SchedulingClass", ctypes.c_ulong),
        ("Padding", ctypes.c_ulong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_job_handle: int | None = None
_job_unavailable = False
_job_lock = threading.Lock()


def _kill_job_handle() -> int | None:
    """懒创建一个「最后一个句柄关闭就杀掉成员」的作业对象。

    我们只把**自己拉起**的子进程挂进去，本进程自己不进作业：这样本进程正常退出
    或被强行结束时，作业句柄随进程表一起关掉，内核立刻收掉里面的 worker /
    llama-server——不依赖我们来得及跑清理代码。
    """
    global _job_handle, _job_unavailable
    if _job_handle or _job_unavailable:
        return _job_handle
    with _job_lock:
        if _job_handle or _job_unavailable:
            return _job_handle
        handle = 0
        try:
            api = _kernel32()
            handle = int(api.CreateJobObjectW(None, None) or 0)
            if not handle:
                raise OSError("CreateJobObjectW 返回空句柄")
            info = _ExtendedLimitInformation()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = api.SetInformationJobObject(
                ctypes.c_void_p(handle),
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                raise OSError("SetInformationJobObject 失败（本进程可能已经属于另一个作业）")
        except Exception:  # noqa: BLE001 - 建不出来就不指望它，另有看门狗与启动清扫
            if handle:
                _kernel32().CloseHandle(ctypes.c_void_p(handle))
            _job_unavailable = True
            _job_handle = None
            return None
        _job_handle = handle
    return _job_handle


def assign_pid_to_kill_job(pid: int, *, logger: Any = None) -> bool:
    """把子进程挂进「随本进程结束一起被内核收掉」的作业对象（尽力而为）。

    失败是很正常的：本进程自己可能已经在一个作业里（某些沙箱 / 启动器会这么做），
    Windows 不允许同时属于两个不同作业。这时退回另外两层保障——子进程里的父进程
    看门狗，以及下次启动时按镜像名清扫孤儿。
    """
    if os.name != "nt" or pid <= 0:
        return False
    job = _kill_job_handle()
    if not job:
        if logger is not None:
            logger.info("作业对象护栏不可用（本进程已在别的作业里？），改用看门狗与启动清扫")
        return False
    api = _kernel32()
    handle = api.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False
    try:
        ok = bool(api.AssignProcessToJobObject(ctypes.c_void_p(job), ctypes.c_void_p(handle)))
    finally:
        api.CloseHandle(ctypes.c_void_p(handle))
    if not ok and logger is not None:
        logger.info("PID %s 挂不进作业对象（本进程可能已在别的作业里），依赖看门狗与启动清扫收尾", pid)
    return ok


def start_parent_watchdog(parent_pid: int | None = None, *, check_seconds: float = 2.0) -> bool:
    """在子进程里调用：父进程一消失，本进程立刻自我结束（不占着显存等死）。

    返回是否真的启动了看门狗。父进程没给、或给的是自己/非法值时返回 False。
    """
    try:
        owner = int(os.getpid() if parent_pid is None else parent_pid)
    except (TypeError, ValueError):
        return False
    if owner <= 0 or owner == os.getpid():
        return False
    interval = max(1.0, float(check_seconds))

    def _watch() -> None:
        while True:
            if process_alive(owner):
                threading.Event().wait(interval)
                continue
            try:
                sys.stderr.write(f"[chenguai] 父进程 {owner} 已退出，worker 自行结束\n")
                sys.stderr.flush()
            except Exception:  # noqa: BLE001 - 管道可能已经关了，写不出去也无所谓
                pass
            os._exit(0)

    thread = threading.Thread(target=_watch, name="chenguai-parent-watchdog", daemon=True)
    thread.start()
    return True


__all__ = [
    "assign_pid_to_kill_job",
    "orphan_pids_by_image",
    "parent_pid_of",
    "process_alive",
    "start_parent_watchdog",
]
