"""Optional real upstream execution, using only the shipped offline fixture."""

import os
import time
from pathlib import Path

import pytest

from quant_studio.experiments import ExperimentStore
from quant_studio.jobs import JobManager
from quant_studio.recipes import RecipeStore
from quant_studio.research_batches import BatchStore, BatchSupervisor
from quant_studio.research_resources import ResourceStore
from quant_studio.server import _job_executor
from quant_studio.templates import load_template


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_BATCH_PYTHON")
    or not os.environ.get("QUANT_TEST_BATCH_SOURCE"),
    reason="optional independent offline fixture runtime",
)
def test_real_batch_runs_preflight_then_execution_and_records_resources(tmp_path):
    source = Path(os.environ["QUANT_TEST_BATCH_SOURCE"])
    profile = {
        "schema_version": "quant-studio.settings/v1",
        "environment": {"QUANT_WORKSPACE_ROOT": str(source.parent)},
        "python_by_repo": {"quant-crypto-basis": os.environ["QUANT_TEST_BATCH_PYTHON"]},
    }
    template = load_template("crypto-basis-fixture")
    recipe = RecipeStore(tmp_path).save(
        "真实离线批次验收", template.id, template.base_config
    )
    store = BatchStore(tmp_path)
    batch = store.prepare(
        recipe["id"],
        recipe["revision"],
        {"seed": [7, 9]},
        max_candidates=2,
        budget={"wall_seconds": 90},
    )
    manager = JobManager(tmp_path, execute=_job_executor(tmp_path))
    supervisor = BatchSupervisor(manager, store=store, profile=profile)
    try:
        supervisor.submit(batch["batch_id"], submission_key="offline-test")
        deadline = time.monotonic() + 180
        while True:
            record = store.get(batch["batch_id"])
            if record["status"] in {"completed", "interrupted"}:
                break
            assert time.monotonic() < deadline, record
            time.sleep(0.1)
        assert record["summary"]["succeeded"] == 2, record
        runs = []
        for candidate in record["candidates"]:
            attempt = candidate["attempts"][0]
            check = manager.get(attempt["preflight_job_id"])
            job = manager.get(attempt["execution_job_id"])
            assert check["status"] == "checked"
            assert job["status"] == "succeeded"
            assert check["created_at"] < job["created_at"]
            usage = ResourceStore(tmp_path).get(job["job_id"])
            assert usage["output_bytes"] > 0
            assert usage["running_seconds"] > 0
            assert usage["termination_reason"] is None
            runs.append(job["run_id"])
        assert ExperimentStore(tmp_path).compare(runs)["comparable"]
    finally:
        supervisor.close()
        manager.close()
