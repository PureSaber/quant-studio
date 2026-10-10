import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
from quant_studio.research_resources import (
    MonitoredControl,
    ResourceBudget,
    ResourceMonitor,
    ResourceStore,
    sample_process_tree_rss,
)
from quant_studio.runner import ExecutionCancelled, _execute_subprocess


def _wait(manager, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = manager.get(job_id)
        if record["status"] not in {"queued", "running", "cancelling"}:
            return record
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


def test_budget_validation_is_explicit_and_does_not_accept_bools():
    assert ResourceBudget(
        wall_seconds=1.5, memory_bytes=1024, output_bytes=2048
    ).as_json()
    for kwargs in [
        {"wall_seconds": True},
        {"wall_seconds": 0},
        {"wall_seconds": 1, "memory_bytes": -1},
        {"wall_seconds": 1, "output_bytes": 0},
    ]:
        with pytest.raises(QuantStudioError):
            ResourceBudget(**kwargs)


def test_real_child_wall_limit_cancels_process_and_persists_usage(tmp_path):
    resources = ResourceStore(tmp_path)
    queued_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    def execute(job, control):
        monitor = ResourceMonitor(
            job["job_id"],
            ResourceBudget(wall_seconds=0.35),
            store=resources,
            queued_at=queued_at,
            sample_interval=0.03,
        )
        wrapped = MonitoredControl(control, monitor)
        try:
            _execute_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                env=None,
                timeout=60,
                control=wrapped,
            )
        except ExecutionCancelled:
            return {"status": "cancelled", "run_id": job["job_id"]}
        finally:
            wrapped.close()
        return {"status": "succeeded", "run_id": job["job_id"]}

    manager = JobManager(tmp_path, execute=execute)
    try:
        job = manager.submit("execute", "synthetic-demo", {}, profile=None)
        stopped = _wait(manager, job["job_id"])
        assert stopped["status"] == "cancelled"
        usage = resources.get(job["job_id"])
        assert usage["termination_reason"] == "wall_time"
        assert usage["running_seconds"] >= 0.3
        assert usage["queue_wait_seconds"] >= 0.8
        assert usage["limits"]["wall_time"] == "exceeded"
        assert usage["limits"]["memory"] == "not_configured"
    finally:
        manager.close()


def test_process_tree_rss_sampling_observes_child_memory():
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; payload=bytearray(12_000_000); "
            "print('ready', flush=True); sys.stdin.read(1)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        observed = sample_process_tree_rss(process.pid)
        assert observed is not None and observed > 1_000_000
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def test_output_limit_uses_attached_directory_and_sampling_failure_is_not_pass(
    tmp_path, monkeypatch
):
    output = tmp_path / "run"
    output.mkdir()
    (output / "large.bin").write_bytes(b"x" * 4096)
    cancelled = []

    class Control:
        cancel_requested = False

        def bind_process(self, process):
            self.process = process

        def release_process(self, process):
            assert process is self.process

        def request_cancel(self, *, force=False):
            self.cancel_requested = True
            cancelled.append(force)
            return True

    monkeypatch.setattr(
        "quant_studio.research_resources.sample_process_tree_rss",
        lambda _pid: (_ for _ in ()).throw(OSError("sampling unavailable")),
    )
    monitor = ResourceMonitor(
        "resource-test",
        ResourceBudget(wall_seconds=5, memory_bytes=1024, output_bytes=1024),
        store=ResourceStore(tmp_path),
        sample_interval=0.02,
    )
    control = Control()
    wrapped = MonitoredControl(control, monitor)
    wrapped.attach_output(output)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        wrapped.bind_process(process)
        deadline = time.monotonic() + 3
        while not control.cancel_requested and time.monotonic() < deadline:
            time.sleep(0.02)
        assert control.cancel_requested
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        wrapped.release_process(process)
    finally:
        if process.poll() is None:
            process.kill()
        wrapped.close()
    usage = ResourceStore(tmp_path).get("resource-test")
    assert usage["termination_reason"] == "output_size"
    assert usage["output_bytes"] >= 4096
    assert usage["peak_rss_bytes"] is None
    assert usage["limits"]["memory"] == "unavailable"
    assert usage["limits"]["output"] == "exceeded"
    assert cancelled


def test_memory_limit_requests_cancel_and_records_observed_peak(tmp_path, monkeypatch):
    class Control:
        cancel_requested = False

        def bind_process(self, process):
            self.process = process

        def release_process(self, process):
            assert process is self.process

        def request_cancel(self, *, force=False):
            self.cancel_requested = True
            return True

    monkeypatch.setattr(
        "quant_studio.research_resources.sample_process_tree_rss", lambda _pid: 4096
    )
    monitor = ResourceMonitor(
        "memory-test",
        ResourceBudget(wall_seconds=5, memory_bytes=1024),
        store=ResourceStore(tmp_path),
        sample_interval=0.02,
    )
    control = Control()
    wrapped = MonitoredControl(control, monitor)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        wrapped.bind_process(process)
        deadline = time.monotonic() + 3
        while not control.cancel_requested and time.monotonic() < deadline:
            time.sleep(0.02)
        assert control.cancel_requested
        process.terminate()
        process.wait(timeout=5)
        wrapped.release_process(process)
    finally:
        if process.poll() is None:
            process.kill()
        wrapped.close()
    usage = ResourceStore(tmp_path).get("memory-test")
    assert usage["termination_reason"] == "memory"
    assert usage["peak_rss_bytes"] == 4096
    assert usage["limits"]["memory"] == "exceeded"


@pytest.mark.skipif(os.name != "nt", reason="Windows进程树实际采样验收")
def test_windows_sampler_includes_grandchild(tmp_path):
    marker = tmp_path / "pid.txt"
    grandchild = (
        "import os,time; from pathlib import Path; "
        "payload=bytearray(18_000_000); "
        f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(5)"
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]); time.sleep(5)",
        ]
    )
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.03)
        assert marker.exists()
        assert sample_process_tree_rss(child.pid) > 10_000_000
    finally:
        child.terminate()
        child.wait(timeout=5)
