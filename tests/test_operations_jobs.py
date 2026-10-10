import base64

import pytest

from quant_studio import QuantStudioError
from quant_studio.intake_tools import IntakeWorkspace
from quant_studio.jobs import JobManager


def test_dispatch_is_idempotent_across_restart_and_rejects_changed_request(tmp_path):
    manager = JobManager(tmp_path, start_worker=False)
    try:
        first = manager.submit(
            "check",
            "synthetic-demo",
            {"seed": 3},
            profile=None,
            dispatch_key="a" * 64,
            batch={"batch_id": "one"},
        )
        repeated = manager.submit(
            "check",
            "synthetic-demo",
            {"seed": 3},
            profile=None,
            dispatch_key="a" * 64,
            batch={"batch_id": "one"},
        )
        assert repeated["job_id"] == first["job_id"]
        with pytest.raises(QuantStudioError, match="身份"):
            manager.submit(
                "check",
                "synthetic-demo",
                {"seed": 4},
                profile=None,
                dispatch_key="a" * 64,
                batch={"batch_id": "one"},
            )
    finally:
        manager.close()
    restarted = JobManager(tmp_path, start_worker=False)
    try:
        repeated = restarted.submit(
            "check",
            "synthetic-demo",
            {"seed": 3},
            profile=None,
            dispatch_key="a" * 64,
            batch={"batch_id": "one"},
        )
        assert repeated["job_id"] == first["job_id"]
        assert repeated["status"] == "interrupted"
        assert len(restarted.list()) == 1
    finally:
        restarted.close()


def test_intake_cancel_and_restart_update_retained_request(tmp_path):
    store = IntakeWorkspace(tmp_path)
    upload = store.upload("input.csv", base64.b64encode(b"id\n001\n").decode())
    record = store.prepare(upload["id"], "test", {"mapping": {"id": "id"}})
    manager = JobManager(tmp_path, start_worker=False)
    try:
        job = manager.submit(
            "intake",
            "research-intake",
            {},
            profile=None,
            intake={"request_id": record["id"]},
        )
        assert store.request(record["id"])["job_id"] == job["job_id"]
        with pytest.raises(QuantStudioError, match="已提交"):
            manager.submit(
                "intake",
                "research-intake",
                {},
                profile=None,
                intake={"request_id": record["id"]},
            )
        manager.cancel(job["job_id"])
        assert store.request(record["id"])["status"] == "cancelled"
    finally:
        manager.close()
