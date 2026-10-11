import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
from quant_studio.notebook_batches import NotebookBatchStore
from quant_studio.notebooks import NotebookStore
from tests.test_notebook_research import configure
from tests.test_notebook_templates import saved_template


def test_batch_page_handles_a_cancel_request_that_lost_the_completion_race(
    tmp_path, monkeypatch
):
    from quant_studio.notebook_batch_web import iterations_body

    configure(tmp_path, monkeypatch)
    project, _, template = saved_template(tmp_path)
    batches = NotebookBatchStore(tmp_path)
    batch = batches.prepare(
        project["id"],
        project["revision"],
        template["id"],
        {"window": [5]},
        max_candidates=1,
    )
    execution = batch["candidates"][0]["attempts"][0]
    run = batches.notebooks.get(project["id"], execution)
    run.update(status="cancel_failed", message="完成时取消已来不及")
    batches.notebooks._save(batches.notebooks.directory(project["id"], execution), run)
    body = iterations_body(tmp_path, "csrf", project["id"], {})
    assert "未能取消" in body
    assert execution in body


def test_batch_freezes_one_source_and_retries_original_inputs(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    project, draft, template = saved_template(tmp_path)
    batches = NotebookBatchStore(tmp_path)
    batch = batches.prepare(
        project["id"],
        project["revision"],
        template["id"],
        {"window": [5, 10, 20, 60]},
        max_candidates=4,
    )
    assert len(batch["candidates"]) == 4
    notebooks = NotebookStore(tmp_path)
    records = [
        notebooks.get(project["id"], c["attempts"][0]) for c in batch["candidates"]
    ]
    assert len({r["hashes"]["source.ipynb"] for r in records}) == 1
    values = [
        json.loads(
            notebooks.artifact(project["id"], r["id"], "inputs.json").read_text("utf-8")
        )
        for r in records
    ]
    assert [v["parameters"]["window"] for v in values] == [5, 10, 20, 60]
    draft.write_text("changed", encoding="utf-8")
    manager = JobManager(tmp_path, start_worker=False)
    try:
        batches.submit(project["id"], batch["id"], manager)
        batches.submit(project["id"], batch["id"], manager)
        assert len(manager.list()) == 4
        manager.cancel(manager.list()[0]["job_id"])
    finally:
        manager.close()
    manager = JobManager(tmp_path, start_worker=False)
    try:
        current = batches.get(project["id"], batch["id"])
        old = current["candidates"][0]["attempts"][-1]
        newer = batches.retry(project["id"], batch["id"], old, manager)
        assert len(newer["candidates"][0]["attempts"]) == 2
        retried = notebooks.get(project["id"], newer["candidates"][0]["attempts"][-1])
        assert retried["hashes"] == records[0]["hashes"]
        with pytest.raises(QuantStudioError):
            batches.retry(project["id"], batch["id"], old, manager)
    finally:
        manager.close()


@pytest.mark.parametrize(
    "grid,limit",
    [
        ({"window": [1, 2]}, 1),
        ({"window": [True]}, 4),
        ({"unknown": [2]}, 4),
        ({"window": []}, 4),
    ],
)
def test_invalid_grid_creates_no_experiments(tmp_path, monkeypatch, grid, limit):
    configure(tmp_path, monkeypatch)
    project, _, template = saved_template(tmp_path)
    with pytest.raises(QuantStudioError):
        NotebookBatchStore(tmp_path).prepare(
            project["id"],
            project["revision"],
            template["id"],
            grid,
            max_candidates=limit,
        )
    assert NotebookStore(tmp_path).list(project["id"]) == []


def test_resume_after_one_retry_does_not_revive_cancelled_candidates(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch)
    project, _, template = saved_template(tmp_path)
    batches = NotebookBatchStore(tmp_path)
    batch = batches.prepare(
        project["id"],
        project["revision"],
        template["id"],
        {"window": [5, 10]},
        max_candidates=2,
    )
    manager = JobManager(tmp_path, start_worker=False)
    try:
        batches.cancel(project["id"], batch["id"], manager)
        batches.retry(
            project["id"], batch["id"], batch["candidates"][0]["attempts"][0], manager
        )
        batches.submit(project["id"], batch["id"], manager)
        assert len(manager.list()) == 1
        untouched = NotebookStore(tmp_path).get(
            project["id"], batch["candidates"][1]["attempts"][0]
        )
        assert untouched["status"] == "cancelled"
    finally:
        manager.close()
