import json
import subprocess
import sys
import threading
import time

import pytest

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
from quant_studio.runner import ExecutionCancelled, _execute_subprocess


def _wait(manager, job_id, states, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] in states:
            return job
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


def test_queue_runs_in_order_and_exposes_stages_and_logs(tmp_path):
    release = threading.Event()
    entered = []

    def execute(job, control):
        entered.append(job["job_id"])
        control.stage("native", "原生命令执行中")
        control.log("stdout", f"started {job['job_id']}")
        if len(entered) == 1:
            assert release.wait(5)
        return {"status": "succeeded", "run_id": job["job_id"]}

    manager = JobManager(tmp_path, execute=execute)
    try:
        first = manager.submit("execute", "synthetic-demo", {}, profile=None)
        second = manager.submit("execute", "synthetic-demo", {}, profile=None)
        _wait(manager, first["job_id"], {"running"})
        assert manager.get(second["job_id"])["status"] == "queued"
        release.set()
        assert _wait(manager, first["job_id"], {"succeeded"})["status"] == "succeeded"
        done = _wait(manager, second["job_id"], {"succeeded"})
        assert entered == [first["job_id"], second["job_id"]]
        assert done["stage"] == "finished"
        assert "started" in manager.read_log(second["job_id"])
    finally:
        release.set()
        manager.close()


def test_queued_and_running_jobs_cancel_without_false_success(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def execute(job, control):
        entered.set()
        assert release.wait(5)
        return {"status": "succeeded", "run_id": job["job_id"]}

    manager = JobManager(tmp_path, execute=execute)
    try:
        running = manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert entered.wait(5)
        queued = manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert manager.cancel(queued["job_id"])["status"] == "cancelled"
        requested = manager.cancel(running["job_id"])
        assert requested["status"] == "cancelling"
        release.set()
        failed = _wait(manager, running["job_id"], {"cancel_failed"})
        assert "完成" in failed["message"]
        assert manager.get(queued["job_id"])["status"] == "cancelled"
    finally:
        release.set()
        manager.close()


def test_restart_marks_old_queued_and_running_jobs_interrupted_without_replay(tmp_path):
    root = tmp_path / ".jobs"
    root.mkdir()
    for index, status in enumerate(("queued", "running", "cancelling")):
        (root / f"job-{index}.json").write_text(
            json.dumps(
                {
                    "schema_version": "quant-studio.job/v1",
                    "job_id": f"job-{index}",
                    "action": "execute",
                    "template_id": "synthetic-demo",
                    "status": status,
                    "stage": "native",
                    "message": None,
                    "created_at": "2026-10-08T00:00:00Z",
                    "updated_at": "2026-10-08T00:00:00Z",
                    "owner_instance_id": "old",
                    "run_id": None,
                }
            ),
            encoding="utf-8",
        )
    calls = []
    manager = JobManager(
        tmp_path, execute=lambda *_: calls.append(1), start_worker=False
    )
    try:
        assert calls == []
        for index in range(3):
            job = manager.get(f"job-{index}")
            assert job["status"] == "interrupted"
            assert "重启" in job["message"]
    finally:
        manager.close()


def test_second_manager_cannot_audit_or_take_over_live_runs_root(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def execute(job, control):
        entered.set()
        assert release.wait(5)
        return {"status": "succeeded", "run_id": job["job_id"]}

    manager = JobManager(tmp_path, execute=execute)
    try:
        job = manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert entered.wait(5)
        assert _wait(manager, job["job_id"], {"running"})["status"] == "running"
        with pytest.raises(QuantStudioError, match="独占锁"):
            JobManager(tmp_path, start_worker=False)
        assert manager.get(job["job_id"])["status"] == "running"
        release.set()
        assert _wait(manager, job["job_id"], {"succeeded"})["status"] == "succeeded"
    finally:
        release.set()
        manager.close()


def test_list_waits_for_atomic_record_update_instead_of_skipping_job(
    tmp_path, monkeypatch
):
    manager = JobManager(tmp_path, start_worker=False)
    job = manager.submit("execute", "synthetic-demo", {}, profile=None)
    entered = threading.Event()
    release = threading.Event()
    listed = threading.Event()
    result = []
    original = manager._write

    def slow_write(record):
        entered.set()
        assert release.wait(5)
        original(record)

    monkeypatch.setattr(manager, "_write", slow_write)
    updater = threading.Thread(
        target=manager._update, args=(job["job_id"],), kwargs={"message": "updated"}
    )

    def read_list():
        result.extend(manager.list())
        listed.set()

    reader = threading.Thread(target=read_list)
    try:
        updater.start()
        assert entered.wait(5)
        reader.start()
        assert not listed.wait(0.1)
        release.set()
        updater.join(timeout=5)
        reader.join(timeout=5)
        assert [item["job_id"] for item in result] == [job["job_id"]]
        assert result[0]["message"] == "updated"
    finally:
        release.set()
        updater.join(timeout=5)
        reader.join(timeout=5)
        manager.close()


def test_close_interrupts_queued_jobs_without_starting_them(tmp_path):
    entered = []
    first_started = threading.Event()
    release = threading.Event()

    def execute(job, control):
        entered.append(job["job_id"])
        first_started.set()
        assert release.wait(5)
        return {"status": "succeeded", "run_id": job["job_id"]}

    manager = JobManager(tmp_path, execute=execute)
    first = manager.submit("execute", "synthetic-demo", {}, profile=None)
    assert first_started.wait(5)
    second = manager.submit("execute", "synthetic-demo", {}, profile=None)
    closer = threading.Thread(target=manager.close)
    closer.start()
    try:
        queued = _wait(manager, second["job_id"], {"interrupted"})
        assert "未执行" in queued["message"]
        with pytest.raises(QuantStudioError, match="关闭"):
            manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert entered == [first["job_id"]]
    finally:
        release.set()
        closer.join(timeout=5)
    assert not closer.is_alive()
    assert manager.get(first["job_id"])["status"] == "interrupted"
    assert manager.get(second["job_id"])["status"] == "interrupted"
    assert entered == [first["job_id"]]


def test_timeout_terminates_owned_process_tree_and_drain_is_bounded(tmp_path):
    started = tmp_path / "grandchild-started"
    escaped = tmp_path / "grandchild-escaped"
    grandchild = (
        "from pathlib import Path; import time; "
        f"Path({str(started)!r}).write_text('started'); "
        "time.sleep(2); "
        f"Path({str(escaped)!r}).write_text('escaped')"
    )
    child = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        "time.sleep(30)"
    )

    class Control:
        cancel_requested = False

        def bind_process(self, process):
            self.process = process

        def release_process(self, process):
            assert process is self.process

        def log(self, stream, content):
            pass

    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _execute_subprocess(
            [sys.executable, "-c", child],
            cwd=tmp_path,
            env=None,
            timeout=0.8,
            control=Control(),
        )
    assert time.monotonic() - began < 8
    assert started.is_file()
    time.sleep(2)
    assert not escaped.exists()


def test_bound_native_process_is_cancelled_by_its_live_job_handle(tmp_path):
    started = threading.Event()

    def execute(job, control):
        started.set()
        try:
            _execute_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                env=None,
                timeout=60,
                control=control,
            )
        except ExecutionCancelled:
            return {"status": "cancelled", "run_id": job["job_id"]}
        return {"status": "succeeded", "run_id": job["job_id"]}

    manager = JobManager(tmp_path, execute=execute)
    try:
        job = manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert started.wait(5)
        _wait(manager, job["job_id"], {"running"})
        manager.cancel(job["job_id"])
        stopped = _wait(manager, job["job_id"], {"cancelled", "cancel_failed"}, 10)
        assert stopped["status"] == "cancelled"
        assert stopped["child_pid"] is None
    finally:
        manager.close()
