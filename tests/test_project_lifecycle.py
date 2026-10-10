import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
from quant_studio.notebooks import NotebookStore
from quant_studio.project_assistant import AssistantStore
from quant_studio.projects import ProjectStore
from tests.test_notebook_research import configure


@pytest.mark.parametrize("kind", ["notebook", "assistant"])
def test_duplicate_submission_cancel_and_restart_update_project_record(
    tmp_path, monkeypatch, kind
):
    configure(tmp_path, monkeypatch)
    project = ProjectStore(tmp_path).save("链路", "状态一致性")
    store = NotebookStore(tmp_path) if kind == "notebook" else AssistantStore(tmp_path)

    def prepare():
        if kind == "notebook":
            store.initialize(project["id"])
            return store.prepare(project["id"], project["revision"])
        return store.prepare(project["id"], project["revision"], "检查状态")

    record = prepare()
    payload = {kind: {"project_id": project["id"], "id": record["id"]}}
    manager = JobManager(tmp_path, start_worker=False)
    try:
        job = manager.submit(kind, kind, {}, profile=None, **payload)
        with pytest.raises(QuantStudioError, match="已提交"):
            manager.submit(kind, kind, {}, profile=None, **payload)
        manager.cancel(job["job_id"])
        assert store.get(project["id"], record["id"])["status"] == "cancelled"
        pending = prepare()
        job = manager.submit(
            kind,
            kind,
            {},
            profile=None,
            **{kind: {"project_id": project["id"], "id": pending["id"]}},
        )
    finally:
        manager.close()
    assert store.get(project["id"], pending["id"])["status"] == "interrupted"
    # Reproduce an unclean shutdown from on-disk evidence; never reuse persisted PIDs.
    path = tmp_path / ".jobs" / (job["job_id"] + ".json")
    orphan = json.loads(path.read_text(encoding="utf-8"))
    orphan["status"] = "running"
    path.write_text(json.dumps(orphan), encoding="utf-8")
    manager = JobManager(tmp_path, start_worker=False)
    try:
        assert manager.get(job["job_id"])["status"] == "interrupted"
        assert store.get(project["id"], pending["id"])["status"] == "interrupted"
    finally:
        manager.close()


def test_notebook_input_copy_and_artifact_tampering(tmp_path, monkeypatch):
    from tests.test_data_catalog import dataset

    configure(tmp_path, monkeypatch)
    item = dataset(tmp_path, "symbol,value\n00700,42\n")
    root = tmp_path / "runs"
    project = ProjectStore(root).save("数据", "冻结输入", datasets=[item["id"]])
    store = NotebookStore(root)
    store.initialize(project["id"])
    record = store.prepare(project["id"], project["revision"])
    directory = store.directory(project["id"], record["id"])
    (tmp_path / "source/bars.csv").write_text("changed")
    store.verify_inputs(record)
    assert "00700,42" in (directory / "inputs" / item["id"] / "bars.csv").read_text()
    store.artifact(project["id"], record["id"], "source.ipynb").write_text("changed")
    with pytest.raises(QuantStudioError, match="完整性"):
        store.artifact(project["id"], record["id"], "source.ipynb")
    with pytest.raises(QuantStudioError):
        store.artifact(project["id"], record["id"], "../../outside")


def test_assistant_packet_redacts_paths_and_tokens_and_checks_identity(tmp_path):
    project = ProjectStore(tmp_path).save(
        "证据", "token=secret C:\\private\\account.csv"
    )
    store = AssistantStore(tmp_path)
    record = store.prepare(project["id"], project["revision"], "核对password=private")
    path = store.directory(project["id"], record["id"]) / "context.json"
    context = path.read_text(encoding="utf-8")
    assert "secret" not in context and "private" not in context
    assert "已隐藏" in context
    path.write_text(context + " ", encoding="utf-8")
    with pytest.raises(QuantStudioError, match="变化"):
        store.get(project["id"], record["id"])
