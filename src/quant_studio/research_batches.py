"""Persistent parameter-grid batches built on the existing FIFO job manager."""

from __future__ import annotations

import copy
import hashlib
import inspect
import itertools
import json
import math
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_studio import QuantStudioError
from quant_studio.recipes import RecipeStore, recipe_template
from quant_studio.templates import render_template

SCHEMA = "quant-studio.research-batch/v1"
HARD_MAX_CANDIDATES = 10_000
_ID = re.compile(r"[a-f0-9]{32}\Z")
_REVISION = re.compile(r"[a-f0-9]{64}\Z")
_JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
_ACTIVE_BATCH = {"submitted", "running", "cancelling"}
_ACTIVE_JOB = {"queued", "running", "cancelling"}
_FAILED_CANDIDATE = {"preflight_failed", "execution_failed", "interrupted"}
_TERMINAL_CANDIDATE = {
    "succeeded",
    "preflight_failed",
    "execution_failed",
    "cancelled",
    "interrupted",
}
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def _revision(record: dict) -> str:
    return _digest({key: value for key, value in record.items() if key != "revision"})


def _summary(record: dict) -> dict[str, int]:
    summary = {
        "prepared": 0,
        "active": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for candidate in record["candidates"]:
        status = candidate["status"]
        if status == "prepared":
            summary["prepared"] += 1
        elif status == "succeeded":
            summary["succeeded"] += 1
        elif status in _FAILED_CANDIDATE:
            summary["failed"] += 1
        elif status == "cancelled":
            summary["cancelled"] += 1
        else:
            summary["active"] += 1
    return summary


class BatchStore:
    """Store immutable grids and mutable attempts with atomic, locked updates."""

    def __init__(self, runs_root: str | Path):
        self.runs_root = Path(runs_root).resolve()
        self.root = self.runs_root / ".batches"
        self.root.mkdir(parents=True, exist_ok=True)
        self.recipe_store = RecipeStore(self.runs_root)
        self._lock_path = self.root / "store.lock"
        with _THREAD_LOCKS_GUARD:
            self._thread_lock = _THREAD_LOCKS.setdefault(
                str(self._lock_path), threading.RLock()
            )

    def prepare(
        self,
        recipe_id: str,
        revision: str,
        grid: Mapping[str, object],
        *,
        max_candidates: int,
        budget: Mapping[str, object] | None = None,
    ) -> dict:
        if (
            type(max_candidates) is not int
            or max_candidates < 1
            or max_candidates > HARD_MAX_CANDIDATES
        ):
            raise QuantStudioError(f"最大候选数须为1–{HARD_MAX_CANDIDATES}的显式整数")
        recipe = self.recipe_store.get(recipe_id, revision)
        if recipe["revision"] != revision:
            raise QuantStudioError("批量实验须指定精确的方案版本")
        template = recipe_template(recipe)
        if not template.metadata.get("preflight_argv"):
            raise QuantStudioError("该方案没有独立预检，不能创建批量实验")
        normalized, count = _normalize_grid(grid, template, max_candidates)
        normalized_budget = _normalize_budget(budget)
        batch_id = uuid.uuid4().hex
        candidates = []
        names = list(normalized)
        dimensions = [normalized[name] for name in names]
        for index, values in enumerate(itertools.product(*dimensions), 1):
            knobs = dict(zip(names, values, strict=True))
            render_template(template, knobs)
            candidates.append(
                {
                    "candidate_id": _digest([batch_id, index, knobs])[:32],
                    "index": index,
                    "knobs": copy.deepcopy(knobs),
                    "status": "prepared",
                    "attempts": [],
                }
            )
        if len(candidates) != count:
            raise QuantStudioError("候选数量计算与展开结果不一致")
        now = _now()
        record = {
            "schema_version": SCHEMA,
            "batch_id": batch_id,
            "revision": "",
            "status": "prepared",
            "created_at": now,
            "updated_at": now,
            "recipe": copy.deepcopy(recipe),
            "grid": normalized,
            "candidate_count": count,
            "max_candidates": max_candidates,
            "budget": normalized_budget,
            "submission_key": None,
            "owner_instance_id": None,
            "cancel_requested": False,
            "retry_keys": [],
            "candidates": candidates,
            "summary": {},
        }
        with self._locked():
            self._commit(record)
        return copy.deepcopy(record)

    def get(self, batch_id: str) -> dict:
        with self._locked():
            return copy.deepcopy(self._read(batch_id))

    def list(self, *, query: str = "", status: str = "") -> list[dict]:
        if status and status not in {
            "prepared",
            "submitted",
            "running",
            "cancelling",
            "cancelled",
            "completed",
            "interrupted",
        }:
            raise QuantStudioError("批次状态筛选无效")
        needle = query.casefold().strip()
        records = []
        with self._locked():
            for path in self.root.glob("*.json"):
                try:
                    record = self._read(path.stem)
                except QuantStudioError:
                    continue
                haystack = " ".join(
                    [
                        record["batch_id"],
                        record["recipe"]["name"],
                        record["recipe"]["template_id"],
                        record["recipe"]["revision"],
                    ]
                ).casefold()
                if (not needle or needle in haystack) and (
                    not status or record["status"] == status
                ):
                    records.append(copy.deepcopy(record))
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def submit(
        self,
        batch_id: str,
        *,
        submission_key: str,
        enqueue: Callable[..., dict],
        owner_id: str,
        expected: str | None = None,
    ) -> dict:
        key = _submission_key(submission_key)
        owner = _owner_id(owner_id)
        with self._locked():
            record = self._read(batch_id)
            if record["submission_key"] is not None:
                if record["submission_key"] != key:
                    raise QuantStudioError("该批次已经用另一提交键显式提交")
                return copy.deepcopy(record)
            if expected is not None and record["revision"] != expected:
                raise QuantStudioError("批次已更新，请刷新后再提交")
            if record["status"] != "prepared":
                raise QuantStudioError("只有预览完成的批次可以提交")
            record.update(
                status="submitted",
                submission_key=key,
                owner_instance_id=owner,
            )
            self._start_next(record)
            self._dispatch(record, enqueue)
            self._commit(record)
            return copy.deepcopy(record)

    def status(
        self,
        batch_id: str,
        *,
        lookup: Callable[[str], dict] | None = None,
        enqueue: Callable[..., dict] | None = None,
        owner_id: str | None = None,
    ) -> dict:
        owner = _owner_id(owner_id) if owner_id is not None else None
        with self._locked():
            record = self._read(batch_id)
            changed = self._reconcile(record, lookup) if lookup else False
            same_owner = owner is not None and owner == record["owner_instance_id"]
            if (
                record["status"] in _ACTIVE_BATCH
                and owner is not None
                and not same_owner
            ):
                for candidate in record["candidates"]:
                    if candidate["status"] not in {
                        "dispatching_preflight",
                        "dispatching_execution",
                    }:
                        continue
                    candidate["status"] = "interrupted"
                    attempt = candidate["attempts"][-1]
                    attempt["status"] = "interrupted"
                    attempt["finished_at"] = _now()
                    changed = True
                if not self._has_live_candidate(record) and any(
                    row["status"] in {"prepared", "interrupted"}
                    for row in record["candidates"]
                ):
                    for candidate in record["candidates"]:
                        if candidate["status"] == "prepared":
                            candidate["status"] = "interrupted"
                    record["status"] = "interrupted"
                    changed = True
            elif (
                record["status"] in {"submitted", "running"}
                and not record["cancel_requested"]
                and same_owner
                and enqueue is not None
            ):
                if not self._has_live_candidate(record):
                    changed = self._start_next(record) or changed
                if self._has_dispatch_pending(record):
                    self._dispatch(record, enqueue)
                    changed = True
            changed = self._settle(record) or changed
            if changed:
                self._commit(record)
            return copy.deepcopy(record)

    def cancel(
        self,
        batch_id: str,
        *,
        cancel: Callable[[str], dict],
        expected: str | None = None,
    ) -> dict:
        with self._locked():
            record = self._read(batch_id)
            if expected is not None and record["revision"] != expected:
                raise QuantStudioError("批次已更新，请刷新后再取消")
            if record["status"] in {"completed", "cancelled", "interrupted"}:
                return copy.deepcopy(record)
            record["cancel_requested"] = True
            for candidate in record["candidates"]:
                if candidate["status"] == "prepared":
                    candidate["status"] = "cancelled"
                    continue
                if candidate["status"] in _TERMINAL_CANDIDATE:
                    continue
                attempt = candidate["attempts"][-1]
                job_id = attempt.get("execution_job_id") or attempt.get(
                    "preflight_job_id"
                )
                if not job_id:
                    candidate["status"] = "cancelled"
                    attempt["status"] = "cancelled"
                    attempt["finished_at"] = _now()
                    continue
                try:
                    job = cancel(job_id)
                except QuantStudioError:
                    candidate["status"] = "cancelling"
                    attempt["status"] = "cancelling"
                else:
                    self._apply_cancel_result(candidate, attempt, job)
            record["status"] = (
                "cancelled" if not self._has_live_candidate(record) else "cancelling"
            )
            self._commit(record)
            return copy.deepcopy(record)

    def retry(
        self,
        batch_id: str,
        candidate_ids: list[str],
        *,
        retry_key: str,
        enqueue: Callable[..., dict],
        owner_id: str,
        expected: str | None = None,
    ) -> dict:
        key = _submission_key(retry_key)
        owner = _owner_id(owner_id)
        if not isinstance(candidate_ids, list) or not candidate_ids:
            raise QuantStudioError("须明确选择至少一个失败候选")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise QuantStudioError("重试候选不能重复")
        with self._locked():
            record = self._read(batch_id)
            if key in record["retry_keys"]:
                return copy.deepcopy(record)
            if expected is not None and record["revision"] != expected:
                raise QuantStudioError("批次已更新，请刷新后再重试")
            selected = {
                candidate["candidate_id"]: candidate
                for candidate in record["candidates"]
                if candidate["candidate_id"] in candidate_ids
            }
            if set(selected) != set(candidate_ids):
                raise QuantStudioError("重试候选不属于此批次")
            if any(row["status"] not in _FAILED_CANDIDATE for row in selected.values()):
                raise QuantStudioError("只能显式重试失败或中断的候选")
            for candidate_id in candidate_ids:
                candidate = selected[candidate_id]
                candidate["status"] = "prepared"
                self._new_attempt(candidate)
            record["retry_keys"].append(key)
            record.update(
                status="submitted",
                cancel_requested=False,
                owner_instance_id=owner,
            )
            self._start_next(record)
            self._dispatch(record, enqueue)
            self._commit(record)
            return copy.deepcopy(record)

    def _reconcile(self, record: dict, lookup: Callable[[str], dict]) -> bool:
        changed = False

        def update(mapping: dict, key: str, value) -> None:
            nonlocal changed
            if mapping.get(key) != value:
                mapping[key] = value
                changed = True

        for candidate in record["candidates"]:
            if candidate["status"] in _TERMINAL_CANDIDATE | {"prepared"}:
                continue
            attempt = candidate["attempts"][-1]
            phase = "execution" if attempt.get("execution_job_id") else "preflight"
            job_id = attempt.get(f"{phase}_job_id")
            if not job_id:
                continue
            try:
                job = lookup(job_id)
            except QuantStudioError:
                continue
            job_status = str(job.get("status", ""))
            update(attempt, f"{phase}_status", job_status)
            if job.get("run_id"):
                update(attempt, f"{phase}_run_id", str(job["run_id"]))
            if job_status in _ACTIVE_JOB:
                active_status = f"{phase}_{job_status}"
                update(candidate, "status", active_status)
                update(attempt, "status", active_status)
                continue
            update(attempt, f"{phase}_message", job.get("message"))
            if phase == "preflight" and job_status == "checked":
                update(candidate, "status", "dispatching_execution")
                update(attempt, "status", "dispatching_execution")
                continue
            if phase == "execution" and job_status == "succeeded":
                terminal = "succeeded"
            elif job_status == "cancelled":
                terminal = "cancelled"
            elif job_status == "interrupted":
                terminal = "interrupted"
            else:
                terminal = (
                    "preflight_failed" if phase == "preflight" else "execution_failed"
                )
            update(candidate, "status", terminal)
            update(attempt, "status", terminal)
            if attempt["finished_at"] is None:
                update(attempt, "finished_at", _now())
        return changed

    def _start_next(self, record: dict) -> bool:
        if self._has_live_candidate(record):
            return False
        for candidate in record["candidates"]:
            if candidate["status"] != "prepared":
                continue
            if not candidate["attempts"] or candidate["attempts"][-1]["status"] not in {
                "prepared",
                "dispatching_preflight",
            }:
                self._new_attempt(candidate)
            candidate["status"] = "dispatching_preflight"
            candidate["attempts"][-1]["status"] = "dispatching_preflight"
            record["status"] = "running"
            return True
        return False

    def _new_attempt(self, candidate: dict) -> dict:
        attempt_no = len(candidate["attempts"]) + 1
        attempt = {
            "attempt_no": attempt_no,
            "created_at": _now(),
            "finished_at": None,
            "status": "prepared",
            "preflight_dispatch_key": None,
            "preflight_job_id": None,
            "preflight_status": None,
            "preflight_run_id": None,
            "preflight_message": None,
            "execution_dispatch_key": None,
            "execution_job_id": None,
            "execution_status": None,
            "execution_run_id": None,
            "execution_message": None,
        }
        candidate["attempts"].append(attempt)
        return attempt

    def _dispatch(self, record: dict, enqueue: Callable[..., dict]) -> None:
        candidate = next(
            (
                row
                for row in record["candidates"]
                if row["status"] in {"dispatching_preflight", "dispatching_execution"}
            ),
            None,
        )
        if candidate is None:
            return
        attempt = candidate["attempts"][-1]
        phase = (
            "execution"
            if candidate["status"] == "dispatching_execution"
            else "preflight"
        )
        action = "execute" if phase == "execution" else "check"
        key = _digest(
            [
                record["batch_id"],
                candidate["candidate_id"],
                attempt["attempt_no"],
                phase,
            ]
        )
        attempt[f"{phase}_dispatch_key"] = key
        job = enqueue(
            action=action,
            recipe=copy.deepcopy(record["recipe"]),
            knobs=copy.deepcopy(candidate["knobs"]),
            snapshot=record["recipe"].get("snapshot"),
            dispatch_key=key,
            batch={
                "batch_id": record["batch_id"],
                "candidate_id": candidate["candidate_id"],
                "attempt_no": attempt["attempt_no"],
                "phase": phase,
                "budget": copy.deepcopy(record["budget"]),
            },
        )
        if not isinstance(job, dict) or not _JOB_ID.fullmatch(
            str(job.get("job_id", ""))
        ):
            raise QuantStudioError("批次调度器没有返回合法Job记录")
        attempt[f"{phase}_job_id"] = job["job_id"]
        attempt[f"{phase}_status"] = str(job.get("status") or "queued")
        candidate["status"] = f"{phase}_queued"
        attempt["status"] = candidate["status"]
        record["status"] = "running"

    def _settle(self, record: dict) -> bool:
        previous_status = record["status"]
        previous_summary = record["summary"]
        summary = _summary(record)
        if record["cancel_requested"] and summary["active"] == 0:
            record["status"] = "cancelled"
        elif summary["active"] == 0 and summary["prepared"] == 0:
            if record["status"] != "interrupted":
                record["status"] = "completed"
        elif record["status"] in _ACTIVE_BATCH:
            record["status"] = "cancelling" if record["cancel_requested"] else "running"
        record["summary"] = summary
        return record["status"] != previous_status or summary != previous_summary

    @staticmethod
    def _apply_cancel_result(candidate: dict, attempt: dict, job: dict) -> None:
        status = str(job.get("status", ""))
        if status == "cancelled":
            candidate["status"] = "cancelled"
            attempt["status"] = "cancelled"
            attempt["finished_at"] = _now()
        else:
            candidate["status"] = "cancelling"
            attempt["status"] = "cancelling"

    @staticmethod
    def _has_live_candidate(record: dict) -> bool:
        return any(
            row["status"] not in _TERMINAL_CANDIDATE | {"prepared"}
            for row in record["candidates"]
        )

    @staticmethod
    def _has_dispatch_pending(record: dict) -> bool:
        return any(
            row["status"] in {"dispatching_preflight", "dispatching_execution"}
            for row in record["candidates"]
        )

    def _commit(self, record: dict) -> None:
        record["updated_at"] = _now()
        record["summary"] = _summary(record)
        record["revision"] = _revision(record)
        _validate(record)
        data = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode()
        path = self.root / f"{record['batch_id']}.json"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _read(self, batch_id: str) -> dict:
        if not _ID.fullmatch(str(batch_id)):
            raise QuantStudioError("批次标识无效")
        try:
            record = json.loads(
                (self.root / f"{batch_id}.json").read_text(encoding="utf-8")
            )
        except FileNotFoundError as exc:
            raise QuantStudioError("批次不存在") from exc
        except (OSError, ValueError) as exc:
            raise QuantStudioError("批次记录损坏") from exc
        _validate(record)
        return record

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            stream = None
            try:
                stream = self._lock_path.open("a+b")
                if self._lock_path.stat().st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if stream is not None:
                    with suppress(OSError):
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    stream.close()


class BatchSupervisor:
    """Advance persisted active batches even when no browser request is polling."""

    def __init__(
        self,
        manager,
        *,
        store: BatchStore | None = None,
        profile: dict | Callable[[], dict | None] | None = None,
        poll_interval: float = 0.1,
    ):
        if poll_interval <= 0:
            raise QuantStudioError("批次协调轮询间隔须为正数")
        self.manager = manager
        self.store = store or BatchStore(manager.runs_root)
        self.owner_id = _owner_id(manager.instance_id)
        self.enqueue = job_manager_enqueue(manager, profile=profile)
        self.poll_interval = float(poll_interval)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="quant-studio-batches", daemon=True
        )
        self._thread.start()

    def submit(
        self, batch_id: str, *, submission_key: str, expected: str | None = None
    ) -> dict:
        record = self.store.submit(
            batch_id,
            submission_key=submission_key,
            enqueue=self.enqueue,
            owner_id=self.owner_id,
            expected=expected,
        )
        self._wake.set()
        return record

    def retry(
        self,
        batch_id: str,
        candidate_ids: list[str],
        *,
        retry_key: str,
        expected: str | None = None,
    ) -> dict:
        record = self.store.retry(
            batch_id,
            candidate_ids,
            retry_key=retry_key,
            enqueue=self.enqueue,
            owner_id=self.owner_id,
            expected=expected,
        )
        self._wake.set()
        return record

    def cancel(self, batch_id: str, *, expected: str | None = None) -> dict:
        record = self.store.cancel(
            batch_id, cancel=self.manager.cancel, expected=expected
        )
        self._wake.set()
        return record

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._wake.set()
        if self._thread is not threading.current_thread():
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            for record in self.store.list():
                if self._stop.is_set():
                    return
                if record["status"] not in _ACTIVE_BATCH:
                    continue
                try:
                    self.store.status(
                        record["batch_id"],
                        lookup=self.manager.get,
                        enqueue=self.enqueue,
                        owner_id=self.owner_id,
                    )
                except (OSError, QuantStudioError):
                    # Persisted records and JobManager diagnostics remain authoritative;
                    # a later poll retries without inventing a successful transition.
                    continue
            self._wake.wait(self.poll_interval)
            self._wake.clear()


def job_manager_enqueue(manager, *, profile=None) -> Callable[..., dict]:
    """Adapt a JobManager to BatchStore's deterministic dispatch contract."""

    parameters = inspect.signature(manager.submit).parameters
    supports_dispatch = "dispatch_key" in parameters
    supports_batch = "batch" in parameters

    def enqueue(**request) -> dict:
        selected_profile = profile() if callable(profile) else profile
        options = {
            "snapshot": request["snapshot"],
            "profile": selected_profile,
            "recipe": request["recipe"],
        }
        if supports_dispatch:
            options["dispatch_key"] = request["dispatch_key"]
        if supports_batch:
            options["batch"] = request["batch"]
        return manager.submit(
            request["action"],
            request["recipe"]["template_id"],
            request["knobs"],
            **options,
        )

    return enqueue


def _normalize_grid(grid, template, max_candidates: int) -> tuple[dict, int]:
    if not isinstance(grid, Mapping) or not grid:
        raise QuantStudioError("参数网格须为至少包含一个参数的对象")
    declared = {item["name"] for item in template.knobs}
    normalized: dict[str, list[Any]] = {}
    count = 1
    for name, values in grid.items():
        if not isinstance(name, str) or name not in declared:
            raise QuantStudioError(f"未知参数 {name}")
        if not isinstance(values, (list, tuple)) or not values:
            raise QuantStudioError(f"参数 {name} 须使用非空有限数组")
        count *= len(values)
        if count > max_candidates:
            raise QuantStudioError(
                f"候选数 {count} 超过显式上限 {max_candidates}；未展开参数网格"
            )
        normalized[name] = copy.deepcopy(list(values))
    return normalized, count


def _normalize_budget(value: Mapping[str, object] | None) -> dict | None:
    if value is None:
        return None
    from quant_studio.research_resources import ResourceBudget

    if not isinstance(value, Mapping):
        raise QuantStudioError("资源预算须为对象")
    unknown = set(value) - {"wall_seconds", "memory_bytes", "output_bytes"}
    if unknown:
        raise QuantStudioError("资源预算包含未知字段")
    try:
        budget = ResourceBudget(**dict(value))
    except TypeError as exc:
        raise QuantStudioError("资源预算字段不完整") from exc
    return budget.as_json()


def _submission_key(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise QuantStudioError("提交键须为1–160个字符")
    return value


def _owner_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", value):
        raise QuantStudioError("批次协调器实例标识无效")
    return value


def _validate(record: object) -> None:
    required = {
        "schema_version",
        "batch_id",
        "revision",
        "status",
        "created_at",
        "updated_at",
        "recipe",
        "grid",
        "candidate_count",
        "max_candidates",
        "budget",
        "submission_key",
        "owner_instance_id",
        "cancel_requested",
        "retry_keys",
        "candidates",
        "summary",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise QuantStudioError("批次记录格式错误")
    if (
        record["schema_version"] != SCHEMA
        or not _ID.fullmatch(str(record["batch_id"]))
        or not _REVISION.fullmatch(str(record["revision"]))
        or record["revision"] != _revision(record)
        or type(record["candidate_count"]) is not int
        or record["candidate_count"] != len(record["candidates"])
        or not isinstance(record["cancel_requested"], bool)
        or not isinstance(record["retry_keys"], list)
    ):
        raise QuantStudioError("批次记录校验失败")
    if record["summary"] != _summary(record):
        raise QuantStudioError("批次汇总与候选记录不一致")
    seen = set()
    for index, candidate in enumerate(record["candidates"], 1):
        if (
            not isinstance(candidate, dict)
            or candidate.get("index") != index
            or not _ID.fullmatch(str(candidate.get("candidate_id", "")))
            or candidate["candidate_id"] in seen
            or not isinstance(candidate.get("knobs"), dict)
            or not isinstance(candidate.get("attempts"), list)
        ):
            raise QuantStudioError("批次候选记录损坏")
        seen.add(candidate["candidate_id"])
    try:
        if not math.isfinite(float(record["candidate_count"])):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise QuantStudioError("批次候选数量无效") from exc


__all__ = ["BatchStore", "BatchSupervisor", "job_manager_enqueue"]
