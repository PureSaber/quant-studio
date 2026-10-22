import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from quant_studio import QuantStudioError
from quant_studio.environment_delivery import DeliveryStore
from quant_studio.jobs import JobManager
from quant_studio.notebook_batches import NotebookBatchStore
from quant_studio.notebook_metrics import compare_notebooks
from quant_studio.notebooks import NotebookStore
from quant_studio.project_assistant import AssistantStore
from quant_studio.projects import ProjectStore
from quant_studio.research_history import search_history
from quant_studio.server import _job_executor
from tests.test_notebook_research import configure
from tests.test_notebook_templates import saved_template
from tests.test_server import _request, _start_http_server


def delivery_setup(tmp_path, monkeypatch):
    config = tmp_path / "delivery.json"
    workspace = tmp_path / "workspace.yaml"
    profile = tmp_path / "profile.json"
    workspace.write_text("root: .\nprojects: {}")
    profile.write_text("{}")
    config.write_text(
        json.dumps(
            {
                "python": sys.executable,
                "source_root": str(tmp_path),
                "workspace_config": str(workspace),
                "profile": str(profile),
                "state_dir": str(tmp_path / "releases"),
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_DELIVERY_CONFIG", str(config))
    return profile


def test_delivery_refuses_drift_and_updates_cancel_state(tmp_path, monkeypatch):
    profile = delivery_setup(tmp_path, monkeypatch)
    store = DeliveryStore(tmp_path)
    record = store.prepare("doctor")
    profile.write_text('{"changed":true}')
    with pytest.raises(QuantStudioError, match="变化"):
        store.execute(record["id"], None)
    record = store.prepare("doctor")
    manager = JobManager(tmp_path, start_worker=False)
    try:
        job = manager.submit(
            "delivery",
            "workspace-delivery",
            {},
            profile=None,
            delivery={"id": record["id"]},
        )
        manager.cancel(job["job_id"])
        assert store.get(record["id"])["status"] == "cancelled"
    finally:
        manager.close()


def test_delivery_reports_native_failures_without_claiming_success(
    tmp_path, monkeypatch
):
    delivery_setup(tmp_path, monkeypatch)
    store = DeliveryStore(tmp_path)
    record = store.prepare("doctor")
    monkeypatch.setattr(
        "quant_studio.runner._execute_subprocess",
        lambda *a, **k: SimpleNamespace(
            returncode=2, stdout='{"status":"blocked","issues":["missing_python"]}'
        ),
    )
    assert store.execute(record["id"], None)["status"] == "failed"
    assert store.get(record["id"])["result_sha256"]


def test_history_keeps_failure_logs_and_previews_code_separately(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    p, _, _ = saved_template(tmp_path)
    n = NotebookStore(tmp_path)
    run = n.prepare(p["id"], p["revision"])
    directory = n.directory(p["id"], run["id"])
    (directory / "execution.log").write_text("KeyError: volume", encoding="utf-8")
    from quant_studio.datasets import file_hash

    run.update(
        status="failed",
        artifacts={"execution.log": file_hash(directory / "execution.log")},
    )
    n._save(directory, run)
    matches = search_history(tmp_path, "KeyError")
    assert matches[0]["status"] == "failed"
    assert "volume" in matches[0]["text"]
    from quant_studio.projects import ProjectStore

    ProjectStore(tmp_path).save(
        "另一项研究",
        "换成完全不同的研究问题",
        project_id=p["id"],
        expected=p["revision"],
    )
    historical = search_history(tmp_path, "KeyError")[0]
    assert historical["title"] == p["name"]
    assert p["question"] in historical["text"]
    assert "换成完全不同" not in historical["text"]
    request = AssistantStore(tmp_path).prepare(
        p["id"], p["revision"], "KeyError怎么修", include_notebook=True
    )
    assert request["notebook_sha256"]
    evidence = json.loads(
        (
            AssistantStore(tmp_path).directory(p["id"], request["id"]) / "context.json"
        ).read_text("utf-8")
    )
    assert any("KeyError" in e["text"] for e in evidence["evidence"])


def test_new_pages_and_forms_use_existing_auth_and_csrf(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    project, _, template = saved_template(tmp_path)
    server, thread = _start_http_server(tmp_path)
    host = f"127.0.0.1:{server.server_port}"
    try:
        for url in [
            "/data-center",
            "/history",
            "/delivery",
            f"/projects/{project['id']}/iterations?template={template['id']}",
        ]:
            status, headers, body = _request(server, "GET", url, host=host)
            if url == "/history":
                assert status == 303
                assert headers["Location"].startswith("/experiments?")
                status, _, body = _request(
                    server, "GET", headers["Location"], host=host
                )
            assert status == 200, (url, body)
        fields = {"action": "doctor"}
        assert (
            _request(
                server,
                "POST",
                "/delivery/prepare",
                host=host,
                origin=f"http://{host}",
                fields=fields,
            )[0]
            == 403
        )
        fields = {
            "revision": project["revision"],
            "template": template["id"],
            "limit": "4",
            "grid_window": "[5,10,20,60]",
            "grid_label": '["测试"]',
            "_csrf_token": "test-only-csrf-token",
        }
        status, headers, body = _request(
            server,
            "POST",
            f"/projects/{project['id']}/iterations-prepare",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 303, body
        assert _request(server, "GET", headers["Location"], host=host)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cell_diff_keeps_added_and_removed_lines_separate(tmp_path):
    import hashlib

    from quant_studio.notebook_proposals import proposal_diff

    path = tmp_path / "source.ipynb"
    source = {"cells": [{"cell_type": "code", "source": "x = 1"}]}
    path.write_text(json.dumps(source), encoding="utf-8")
    patch = {
        "cell": 0,
        "before_sha256": hashlib.sha256(b"x = 1").hexdigest(),
        "source": "x = 2",
        "reason": "修改参数",
        "citations": ["E1"],
    }
    changes = proposal_diff(tmp_path, [patch])
    assert "\n-x = 1\n+x = 2\n" in changes
    assert json.loads(path.read_text("utf-8")) == source
    patch["before_sha256"] = "0" * 64
    with pytest.raises(QuantStudioError, match="源码"):
        proposal_diff(tmp_path, [patch])


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"),
    reason="optional notebook environment",
)
def test_real_four_parameter_runs_continue_without_browser_and_compare(
    tmp_path, monkeypatch
):
    settings = tmp_path / "notebook.json"
    settings.write_text(
        json.dumps(
            {
                "python": os.environ["QUANT_TEST_NOTEBOOK_PYTHON"],
                "lab_url": "http://127.0.0.1:8890",
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(settings))
    project, draft, template = saved_template(tmp_path)
    nb = json.loads(draft.read_text("utf-8"))
    nb["cells"][-1]["source"] = (
        "research.metric('窗口', parameters['window'], "
        "unit='天', definition='输入观察窗口')\n"
        "import pandas as pd\n"
        "values = pd.Series([1., 2., None])\n"
        "assert research.statistics(values)['missing'] == 1\n"
        "research.plot(values, title='窗口研究', ylabel='值')"
    )
    draft.write_text(json.dumps(nb), encoding="utf-8")
    batches = NotebookBatchStore(tmp_path)
    batch = batches.prepare(
        project["id"],
        project["revision"],
        template["id"],
        {"window": [5, 10, 20, 60]},
        max_candidates=4,
    )
    manager = JobManager(tmp_path, execute=_job_executor(tmp_path))
    try:
        batches.submit(project["id"], batch["id"], manager)
        deadline = time.monotonic() + 120
        while (
            any(j["status"] in {"queued", "running"} for j in manager.list())
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        assert all(j["status"] == "succeeded" for j in manager.list()), manager.list()
        refs = [f"{project['id']}:{c['attempts'][0]}" for c in batch["candidates"]]
        comparison = compare_notebooks(tmp_path, refs)
        assert comparison["rows"][0][3:] == [5, 10, 20, 60]
        assert "执行代码不同" not in comparison["warnings"]
        # Saving the conclusion remains the existing immutable project operation.
        saved = ProjectStore(tmp_path).save(
            project["name"],
            project["question"],
            project_id=project["id"],
            expected=project["revision"],
            notes="四组窗口已比较，仅验证研究链路。",
        )
        assert saved["notes"]
    finally:
        manager.close()
