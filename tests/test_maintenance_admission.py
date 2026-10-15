import subprocess
import sys
import threading

import pytest

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager, _admission_lock


@pytest.mark.parametrize("action", ["execute", "delivery"])
def test_maintenance_rejects_before_persistence_and_recovers(tmp_path, action):
    marker = tmp_path / ".local-maintenance.json"
    manager = JobManager(tmp_path, start_worker=False)
    kwargs = {"delivery": {"id": "reviewed"}} if action == "delivery" else {}
    try:
        marker.write_text("{}")
        with pytest.raises(QuantStudioError, match="本机正在维护"):
            manager.submit(action, "synthetic-demo", {}, profile=None, **kwargs)
        assert manager.list() == []
        assert manager._queue.empty()
        assert manager._requests == {}
        marker.unlink()
        # A normal task becomes admissible once the controller clears its marker.
        accepted = manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert accepted["status"] == "queued"
    finally:
        manager.close()


def test_controller_lock_rejects_admission_without_queue_mutation(tmp_path):
    manager = JobManager(tmp_path, start_worker=False)
    try:
        with (
            _admission_lock(tmp_path),
            pytest.raises(QuantStudioError, match="维护检查正在进行"),
        ):
            manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert manager.list() == []
        accepted = manager.submit("execute", "synthetic-demo", {}, profile=None)
        assert accepted["status"] == "queued"
    finally:
        manager.close()


def test_admission_lock_covers_publication_across_processes(tmp_path, monkeypatch):
    manager = JobManager(tmp_path, start_worker=False)
    writing = threading.Event()
    release = threading.Event()
    original_write = manager._write
    result = []

    def delayed_write(record):
        writing.set()
        assert release.wait(10)
        original_write(record)

    monkeypatch.setattr(manager, "_write", delayed_write)

    def submit():
        try:
            result.append(manager.submit("execute", "synthetic-demo", {}, profile=None))
        except Exception as exc:
            result.append(exc)

    thread = threading.Thread(target=submit)
    thread.start()
    try:
        assert writing.wait(5)
        code = """
import sys
from pathlib import Path
from quant_studio import QuantStudioError
from quant_studio.jobs import _admission_lock
try:
    with _admission_lock(Path(sys.argv[1])):
        sys.exit(2)
except QuantStudioError:
    sys.exit(0)
"""
        probe = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert probe.returncode == 0, probe.stderr
        assert list((tmp_path / ".jobs").glob("*.json")) == []
    finally:
        release.set()
        thread.join(10)
        manager.close()
    assert len(result) == 1 and isinstance(result[0], dict), result
    assert result[0]["status"] == "queued"
