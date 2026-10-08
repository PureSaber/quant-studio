"""Persistent, explicit FIFO jobs for the local HTTP workbench."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.runner import _terminate_process_scope
from quant_studio.settings import use_settings

_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
_ACTIVE = {"queued", "running", "cancelling"}


class JobControl:
    def __init__(self, manager: JobManager, job_id: str):
        self.manager = manager
        self.job_id = job_id
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def stage(self, stage: str, message: str | None = None) -> None:
        self.manager._update(self.job_id, stage=stage, message=message)

    def log(self, stream: str, content: str) -> None:
        self.manager._append_log(self.job_id, stream, content)

    def bind_process(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is not None:
                raise RuntimeError("job already has a bound child process")
            self._process = process
        self.manager._update(self.job_id, child_pid=process.pid)

    def release_process(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is process:
                self._process = None
        self.manager._update(self.job_id, child_pid=None)

    def request_cancel(self, *, force: bool = False) -> bool:
        self._cancel.set()
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return False
            return _terminate_bound_process(process, force=force)


class JobManager:
    def __init__(
        self,
        runs_root: str | Path,
        *,
        execute: Callable[[dict, JobControl], dict | object] | None = None,
        start_worker: bool = True,
    ):
        self.runs_root = Path(runs_root).resolve()
        self.root = self.runs_root / ".jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.instance_id = uuid.uuid4().hex
        self._execute = execute or self._default_execute
        self._requests: dict[str, dict] = {}
        self._controls: dict[str, JobControl] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._closed = False
        self._root_lock = _acquire_root_lock(self.root / "manager.lock")
        self._worker = None
        try:
            self._audit_orphans()
            if start_worker:
                self._worker = threading.Thread(
                    target=self._work, name="quant-studio-jobs", daemon=True
                )
                self._worker.start()
        except Exception:
            _release_root_lock(self._root_lock)
            self._root_lock = None
            raise

    def submit(
        self,
        action: str,
        template_id: str,
        knobs: dict,
        *,
        factors: list[str] | None = None,
        snapshot: str | None = None,
        profile: dict | None,
    ) -> dict:
        if action not in {"check", "execute"}:
            raise QuantStudioError("只有预检和显式执行可以进入任务队列")
        job_id = uuid.uuid4().hex
        now = _now()
        record = {
            "schema_version": "quant-studio.job/v1",
            "job_id": job_id,
            "action": action,
            "template_id": template_id,
            "status": "queued",
            "stage": "queued",
            "message": "等待前序任务",
            "created_at": now,
            "updated_at": now,
            "owner_instance_id": self.instance_id,
            "run_id": None,
            "child_pid": None,
        }
        request = {
            **record,
            "knobs": dict(knobs),
            "factors": None if factors is None else list(factors),
            "snapshot": snapshot,
            "profile": profile,
        }
        with self._lock:
            if self._closed:
                raise QuantStudioError("任务队列已经关闭")
            self._requests[job_id] = request
            self._write(record)
            self._queue.put(job_id)
        return dict(record)

    def get(self, job_id: str) -> dict:
        path = self._record_path(job_id)
        with self._lock:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise QuantStudioError("任务不存在") from exc
            except (OSError, ValueError) as exc:
                raise QuantStudioError(f"任务记录损坏：{exc}") from exc
        if not isinstance(value, dict) or value.get("job_id") != job_id:
            raise QuantStudioError("任务记录损坏")
        return value

    def list(self) -> list[dict]:
        records = []
        with self._lock:
            for path in self.root.glob("*.json"):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(value, dict) and value.get("job_id") == path.stem:
                        records.append(value)
                except (OSError, ValueError):
                    continue
        return sorted(
            records, key=lambda item: str(item.get("created_at", "")), reverse=True
        )

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            record = self.get(job_id)
            if record["status"] == "queued":
                return self._update(
                    job_id,
                    status="cancelled",
                    stage="cancelled",
                    message="排队任务已取消；没有启动原生进程。",
                )
            if record["status"] != "running":
                raise QuantStudioError("该任务当前不能取消")
            control = self._controls.get(job_id)
            if control is None:
                return self._update(
                    job_id,
                    status="cancel_failed",
                    stage="finished",
                    message="任务进程句柄不可用，未声称取消成功。",
                )
            terminated = control.request_cancel()
            message = (
                "已向本任务持有的原生进程请求终止。"
                if terminated
                else "取消已请求；进程可能已经结束，等待核对最终状态。"
            )
            return self._update(
                job_id, status="cancelling", stage="cancelling", message=message
            )

    def read_log(self, job_id: str) -> str:
        self._record_path(job_id)
        path = self.root / f"{job_id}.log"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            controls = list(self._controls.values())
            for job_id in list(self._requests):
                try:
                    record = self.get(job_id)
                except QuantStudioError:
                    continue
                if record.get("status") == "queued":
                    self._update(
                        job_id,
                        status="interrupted",
                        stage="finished",
                        message="服务停止前任务尚未开始；未执行且不会自动重放。",
                    )
                    self._requests.pop(job_id, None)
        for control in controls:
            control.request_cancel(force=True)
        self._queue.put(None)
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join()
        _release_root_lock(self._root_lock)
        self._root_lock = None

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                try:
                    record = self.get(job_id)
                except QuantStudioError:
                    continue
                if record["status"] != "queued":
                    continue
                request = self._requests.get(job_id)
                if request is None:
                    self._update(
                        job_id,
                        status="interrupted",
                        stage="finished",
                        message="任务参数不在当前服务实例中，未自动重放。",
                    )
                    continue
                control = JobControl(self, job_id)
                self._controls[job_id] = control
                self._update(
                    job_id,
                    status="running",
                    stage="preparing",
                    message="正在准备本次任务",
                )
            try:
                with use_settings(request.get("profile")):
                    outcome = self._execute(request, control)
                data = outcome.as_json() if hasattr(outcome, "as_json") else outcome
                if not isinstance(data, dict):
                    raise QuantStudioError("任务执行器没有返回结构化结果")
                run_id = data.get("run_id")
                actual = str(data.get("status") or "failed")
                if control.cancel_requested:
                    if self._closed:
                        status = "interrupted"
                        message = "服务停止时中断了本实例持有的任务；不会自动重放。"
                    else:
                        status = (
                            "cancelled" if actual == "cancelled" else "cancel_failed"
                        )
                        message = (
                            "本任务原生进程已终止。"
                            if status == "cancelled"
                            else "取消请求后任务已完成或返回其他状态，未声称取消成功。"
                        )
                else:
                    status = actual
                    message = data.get("message")
                self._update(
                    job_id,
                    status=status,
                    stage="finished",
                    message=message,
                    run_id=run_id,
                    child_pid=None,
                )
            except Exception as exc:  # worker must retain a diagnostic record
                status = (
                    "interrupted"
                    if self._closed
                    else ("cancelled" if control.cancel_requested else "failed")
                )
                message = (
                    f"服务停止时任务中断：{exc}"
                    if self._closed
                    else f"任务执行失败：{exc}"
                )
                self._update(
                    job_id,
                    status=status,
                    stage="finished",
                    message=message,
                    child_pid=None,
                )
            finally:
                with self._lock:
                    self._controls.pop(job_id, None)
                    self._requests.pop(job_id, None)

    def _default_execute(self, job: dict, control: JobControl):
        from quant_studio.runner import preflight, run

        options = {
            "factors": job.get("factors"),
            "snapshot": job.get("snapshot"),
            "runs_root": self.runs_root,
            "control": control,
        }
        if job["action"] == "check":
            return preflight(job["template_id"], job["knobs"], **options)
        return run(job["template_id"], job["knobs"], execute=True, **options)

    def _audit_orphans(self) -> None:
        for path in self.root.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(value, dict) or value.get("status") not in _ACTIVE:
                continue
            value.update(
                status="interrupted",
                stage="finished",
                message="服务重启后发现未完成任务；保留诊断且不会自动重放。",
                updated_at=_now(),
                owner_instance_id=self.instance_id,
                child_pid=None,
            )
            self._write(value)

    def _update(self, job_id: str, **changes) -> dict:
        with self._lock:
            record = self.get(job_id)
            record.update(changes, updated_at=_now())
            self._write(record)
            return dict(record)

    def _append_log(self, job_id: str, stream: str, content: str) -> None:
        if not content:
            return
        self._record_path(job_id)
        line = f"[{_now()}] {stream}: {content.rstrip()}\n"
        with (
            self._lock,
            (self.root / f"{job_id}.log").open("a", encoding="utf-8") as handle,
        ):
            handle.write(line)
            handle.flush()

    def _record_path(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise QuantStudioError("非法任务 id")
        return self.root / f"{job_id}.json"

    def _write(self, value: dict) -> None:
        path = self._record_path(str(value["job_id"]))
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def _terminate_bound_process(process: subprocess.Popen, *, force: bool = False) -> bool:
    # The caller owns this live Popen handle; persisted PIDs are never used.
    return _terminate_process_scope(process, force=force)


def _acquire_root_lock(path: Path):
    try:
        stream = path.open("a+b")
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError as exc:
        with suppress(NameError, OSError):
            stream.close()
        raise QuantStudioError("该 runs_root 已有任务服务持有独占锁") from exc


def _release_root_lock(stream) -> None:
    if stream is None:
        return
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()
