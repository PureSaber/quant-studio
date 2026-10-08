import json
import sys
import threading
import time

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
