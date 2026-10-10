"""Soft resource-budget monitoring for owned research subprocess trees."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_studio import QuantStudioError

SCHEMA = "quant-studio.resource-usage/v1"
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ResourceBudget:
    """Polling soft limits; these values do not create OS-level isolation."""

    wall_seconds: float
    memory_bytes: int | None = None
    output_bytes: int | None = None

    def __post_init__(self):
        if (
            isinstance(self.wall_seconds, bool)
            or not isinstance(self.wall_seconds, (int, float))
            or not math.isfinite(float(self.wall_seconds))
            or self.wall_seconds <= 0
        ):
            raise QuantStudioError("墙钟预算须为正的有限秒数")
        for name, value in (
            ("内存", self.memory_bytes),
            ("输出", self.output_bytes),
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise QuantStudioError(f"{name}预算须为正整数字节数")

    def as_json(self) -> dict[str, int | float | None]:
        return {
            "wall_seconds": float(self.wall_seconds),
            "memory_bytes": self.memory_bytes,
            "output_bytes": self.output_bytes,
        }


class ResourceStore:
    """Persist observations separately from JobManager's authoritative status."""

    def __init__(self, runs_root: str | Path):
        self.root = Path(runs_root).resolve() / ".resources"
        self.root.mkdir(parents=True, exist_ok=True)
        with _STORE_LOCKS_GUARD:
            self._lock = _STORE_LOCKS.setdefault(str(self.root), threading.RLock())

    def get(self, job_id: str) -> dict:
        path = self._path(job_id)
        with self._lock:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise QuantStudioError("资源记录不存在") from exc
            except (OSError, ValueError) as exc:
                raise QuantStudioError("资源记录损坏") from exc
        _validate_record(record, job_id)
        return record

    def write(self, record: dict) -> None:
        job_id = str(record.get("job_id", ""))
        _validate_record(record, job_id)
        path = self._path(job_id)
        data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
        with self._lock:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{path.name}.", dir=self.root
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def _path(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise QuantStudioError("资源记录Job标识无效")
        return self.root / f"{job_id}.json"


class ResourceMonitor:
    """Sample a live owned process tree and request cancellation on soft limits."""

    def __init__(
        self,
        job_id: str,
        budget: ResourceBudget,
        *,
        store: ResourceStore,
        queued_at: str | None = None,
        sample_interval: float = 0.25,
    ):
        if not _JOB_ID.fullmatch(job_id):
            raise QuantStudioError("资源监控Job标识无效")
        if (
            isinstance(sample_interval, bool)
            or not isinstance(sample_interval, (int, float))
            or not math.isfinite(float(sample_interval))
            or sample_interval <= 0
        ):
            raise QuantStudioError("资源采样间隔须为正的有限秒数")
        self.job_id = job_id
        self.budget = budget
        self.store = store
        self.sample_interval = float(sample_interval)
        self._started_wall = datetime.now(UTC)
        self._started_monotonic = time.monotonic()
        self._queued_at = queued_at
        self._control = None
        self._process = None
        self._output_path: Path | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._peak_rss: int | None = None
        self._output_bytes: int | None = None
        self._rss_samples = 0
        self._rss_errors = 0
        self._output_samples = 0
        self._output_errors = 0
        self._termination_reason: str | None = None
        self._record = self._snapshot(finished=False)
        self.store.write(self._record)

    def start(self, control) -> None:
        with self._lock:
            if self._thread is not None:
                if self._control is not control:
                    raise RuntimeError("resource monitor already has a control")
                return
            self._control = control
            self._thread = threading.Thread(
                target=self._run,
                name=f"quant-studio-resource-{self.job_id[:12]}",
                daemon=True,
            )
            self._thread.start()

    def bind_process(self, process) -> None:
        with self._lock:
            self._process = process

    def release_process(self, process) -> None:
        with self._lock:
            if self._process is process:
                self._process = None
        self._sample()

    def attach_output(self, path: str | Path) -> None:
        selected = Path(path).resolve()
        if not selected.is_dir():
            raise QuantStudioError("资源监控输出路径须为已存在目录")
        with self._lock:
            if self._output_path is not None and self._output_path != selected:
                raise QuantStudioError("资源监控不能切换到另一输出目录")
            self._output_path = selected
        self._sample()

    def close(self) -> dict:
        with self._lock:
            if self._closed:
                return dict(self._record)
            self._closed = True
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._sample(enforce=False)
        with self._lock:
            self._record = self._snapshot(finished=True)
            self.store.write(self._record)
            return dict(self._record)

    def _run(self) -> None:
        while not self._stop.wait(self.sample_interval):
            self._sample()

    def _sample(self, *, enforce: bool = True) -> None:
        with self._lock:
            process = self._process
            output_path = self._output_path
        if process is not None and process.poll() is None:
            try:
                rss = sample_process_tree_rss(process.pid)
                if rss is None:
                    self._rss_errors += 1
                else:
                    self._rss_samples += 1
                    self._peak_rss = max(self._peak_rss or 0, rss)
            except (OSError, RuntimeError, ValueError):
                self._rss_errors += 1
        if output_path is not None:
            try:
                self._output_bytes = _directory_size(output_path)
                self._output_samples += 1
            except OSError:
                self._output_errors += 1
        elapsed = time.monotonic() - self._started_monotonic
        reason = None
        if elapsed >= self.budget.wall_seconds:
            reason = "wall_time"
        elif (
            self.budget.memory_bytes is not None
            and self._peak_rss is not None
            and self._peak_rss > self.budget.memory_bytes
        ):
            reason = "memory"
        elif (
            self.budget.output_bytes is not None
            and self._output_bytes is not None
            and self._output_bytes > self.budget.output_bytes
        ):
            reason = "output_size"
        if enforce and reason is not None:
            self._trigger(reason)
        with self._lock:
            self._record = self._snapshot(finished=False)
            self.store.write(self._record)

    def _trigger(self, reason: str) -> None:
        with self._lock:
            if self._termination_reason is not None:
                return
            self._termination_reason = reason
            control = self._control
        if control is not None:
            control.request_cancel()

    def _snapshot(self, *, finished: bool) -> dict[str, Any]:
        elapsed = max(0.0, time.monotonic() - self._started_monotonic)
        limits = {
            "wall_time": _limit_state(
                configured=True,
                exceeded=self._termination_reason == "wall_time",
                samples=1,
                errors=0,
                finished=finished,
            ),
            "memory": _limit_state(
                configured=self.budget.memory_bytes is not None,
                exceeded=self._termination_reason == "memory",
                samples=self._rss_samples,
                errors=self._rss_errors,
                finished=finished,
            ),
            "output": _limit_state(
                configured=self.budget.output_bytes is not None,
                exceeded=self._termination_reason == "output_size",
                samples=self._output_samples,
                errors=self._output_errors,
                finished=finished,
            ),
        }
        return {
            "schema_version": SCHEMA,
            "job_id": self.job_id,
            "queued_at": self._queued_at,
            "started_at": self._started_wall.isoformat(),
            "finished_at": _now() if finished else None,
            "queue_wait_seconds": _queue_wait(self._queued_at, self._started_wall),
            "running_seconds": elapsed,
            "peak_rss_bytes": self._peak_rss,
            "output_bytes": self._output_bytes,
            "termination_reason": self._termination_reason,
            "budget": self.budget.as_json(),
            "limits": limits,
            "sampling": {
                "interval_seconds": self.sample_interval,
                "rss_samples": self._rss_samples,
                "rss_errors": self._rss_errors,
                "output_samples": self._output_samples,
                "output_errors": self._output_errors,
            },
            "enforcement": "polling_soft_limit",
        }


class MonitoredControl:
    """JobControl-compatible proxy that adds resource observations."""

    def __init__(self, control, monitor: ResourceMonitor):
        self.control = control
        self.monitor = monitor
        self.monitor.start(control)

    @property
    def cancel_requested(self) -> bool:
        return bool(self.control.cancel_requested)

    def stage(self, stage: str, message: str | None = None) -> None:
        self.control.stage(stage, message)

    def log(self, stream: str, content: str) -> None:
        self.control.log(stream, content)

    def bind_process(self, process) -> None:
        self.control.bind_process(process)
        self.monitor.bind_process(process)

    def release_process(self, process) -> None:
        self.monitor.release_process(process)
        self.control.release_process(process)

    def request_cancel(self, *, force: bool = False) -> bool:
        return self.control.request_cancel(force=force)

    def attach_output(self, path: str | Path) -> None:
        self.monitor.attach_output(path)

    def close(self) -> dict:
        return self.monitor.close()

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()


def sample_process_tree_rss(root_pid: int) -> int | None:
    """Return observed RSS for root and descendants without reading argv or env."""

    if type(root_pid) is not int or root_pid <= 0:
        raise ValueError("pid must be a positive integer")
    if os.name == "nt":
        return _windows_tree_rss(root_pid)
    if os.name == "posix" and Path("/proc").is_dir():
        return _proc_tree_rss(root_pid)
    return None


def _proc_tree_rss(root_pid: int) -> int | None:
    pending = [root_pid]
    seen: set[int] = set()
    total = 0
    samples = 0
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        children = Path(f"/proc/{pid}/task/{pid}/children")
        with suppress(FileNotFoundError, PermissionError, ValueError):
            pending.extend(int(value) for value in children.read_text().split())
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
        except (FileNotFoundError, PermissionError):
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1]) * 1024
                samples += 1
                break
    return total if samples else None


def _windows_tree_rss(root_pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    ulong_ptr = wintypes.WPARAM

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ulong_ptr),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    first = kernel32.Process32FirstW
    first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    first.restype = wintypes.BOOL
    following = kernel32.Process32NextW
    following.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32)]
    following.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    snapshot = create_snapshot(0x00000002, 0)
    invalid = wintypes.HANDLE(-1).value
    if snapshot == invalid:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    parents: dict[int, list[int]] = {}
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = first(snapshot, ctypes.byref(entry))
        while ok:
            parents.setdefault(int(entry.th32ParentProcessID), []).append(
                int(entry.th32ProcessID)
            )
            ok = following(snapshot, ctypes.byref(entry))
    finally:
        close_handle(snapshot)

    pids, pending = [], [root_pid]
    seen = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        pids.append(pid)
        pending.extend(parents.get(pid, []))

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_memory = psapi.GetProcessMemoryInfo
    get_memory.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_memory.restype = wintypes.BOOL
    total = 0
    samples = 0
    for pid in pids:
        handle = open_process(0x0400 | 0x0010, False, pid)
        if not handle:
            continue
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if get_memory(handle, ctypes.byref(counters), counters.cb):
                total += int(counters.WorkingSetSize)
                samples += 1
        finally:
            close_handle(handle)
    return total if samples else None


def _directory_size(root: Path) -> int:
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def _queue_wait(queued_at: str | None, started: datetime) -> float | None:
    if queued_at is None:
        return None
    try:
        queued = datetime.fromisoformat(queued_at.replace("Z", "+00:00"))
        if queued.tzinfo is None:
            raise ValueError
        return max(0.0, (started - queued.astimezone(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _limit_state(
    *, configured: bool, exceeded: bool, samples: int, errors: int, finished: bool
) -> str:
    if not configured:
        return "not_configured"
    if exceeded:
        return "exceeded"
    if not finished:
        return "monitoring"
    if samples == 0:
        return "unavailable"
    if errors:
        return "incomplete"
    return "observed_within_limit"


def _validate_record(record: object, job_id: str) -> None:
    required = {
        "schema_version",
        "job_id",
        "queued_at",
        "started_at",
        "finished_at",
        "queue_wait_seconds",
        "running_seconds",
        "peak_rss_bytes",
        "output_bytes",
        "termination_reason",
        "budget",
        "limits",
        "sampling",
        "enforcement",
    }
    if (
        not isinstance(record, dict)
        or set(record) != required
        or record["schema_version"] != SCHEMA
        or record["job_id"] != job_id
        or not _JOB_ID.fullmatch(job_id)
        or record["enforcement"] != "polling_soft_limit"
        or record["termination_reason"]
        not in {None, "wall_time", "memory", "output_size"}
    ):
        raise QuantStudioError("资源记录格式错误")


__all__ = [
    "MonitoredControl",
    "ResourceBudget",
    "ResourceMonitor",
    "ResourceStore",
    "sample_process_tree_rss",
]
