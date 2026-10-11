import json
import os
import subprocess

import pytest

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore
from quant_studio.jobs import JobManager
from quant_studio.notebooks import NotebookStore
from quant_studio.project_evidence import collect_project, project_evidence
from quant_studio.project_evidence_web import evidence_body
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore, recipe_template
from quant_studio.runner import run
from tests.test_server import _request, _start_http_server


def configuration(tmp_path, monkeypatch):
    python = os.environ.get("QUANT_TEST_RESEARCH_AGENT_PYTHON")
    if not python:
        pytest.skip(
            "requires installed federation Agent and Notebook kernel environment"
        )
    config = tmp_path / "runtimes.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {"quant-agent": python},
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_RUNTIMES", str(config))
    notebook = tmp_path / "notebook.json"
    notebook.write_text(
        json.dumps({"python": python, "lab_url": "http://127.0.0.1:8890/"})
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(notebook))
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path / "ipython"))
    monkeypatch.setenv("JUPYTER_RUNTIME_DIR", str(tmp_path / "jupyter"))
    return python


def test_missing_agent_environment_is_explicit_and_readonly(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    project = ProjectStore(tmp_path).save("研究", "假设")
    body = evidence_body(tmp_path, "csrf", project["id"])
    assert "需要已配置" in body
    assert not (tmp_path / ".projects" / project["id"] / "research-review").exists()


def test_exact_project_binding_excludes_other_project_jobs(tmp_path):
    recipe = RecipeStore(tmp_path).save("原生样例", "synthetic-demo", {})
    refs = [{"id": recipe["id"], "revision": recipe["revision"]}]
    projects = [
        ProjectStore(tmp_path).save(str(i), "问题", recipes=refs) for i in range(2)
    ]
    manager = JobManager(tmp_path, start_worker=False)
    try:
        for project in projects:
            manager.submit(
                "workflow",
                "synthetic-demo",
                {},
                profile=None,
                recipe=recipe,
                research_project={"id": project["id"], "revision": project["revision"]},
            )
        for project in projects:
            view = collect_project(tmp_path, project["id"])
            assert len(view["attempts"]) == 1
            assert view["attempts"][0]["status"] == "queued"
            assert view["attempts"][0]["association"] == "explicit_project"
        unrelated = ProjectStore(tmp_path).save("无方案", "问题")
        with pytest.raises(QuantStudioError, match="未连接"):
            manager.submit(
                "workflow",
                "synthetic-demo",
                {},
                profile=None,
                recipe=recipe,
                research_project={
                    "id": unrelated["id"],
                    "revision": unrelated["revision"],
                },
            )
    finally:
        manager.close()
    assert (
        collect_project(tmp_path, projects[0]["id"])["attempts"][0]["status"]
        == "interrupted"
    )


def test_real_notebook_native_paths_review_and_source_drift(tmp_path, monkeypatch):
    python = configuration(tmp_path, monkeypatch)
    root = tmp_path / "runs"
    data = tmp_path / "data"
    data.mkdir()
    (data / "prices.csv").write_text("date,value\n2026-01-01,42\n")
    dataset = DatasetStore(root).register("合成输入", "a-share-four-factor", str(data))
    recipe = RecipeStore(root).save("原生样例", "synthetic-demo", {})
    project = ProjectStore(root).save(
        "统一研究",
        "合成证据能否贯通",
        datasets=[dataset["id"]],
        recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
    )
    notebooks = NotebookStore(root)
    draft = notebooks.initialize(project["id"])
    for code, status in [
        ("print(42)", "succeeded"),
        ("raise ValueError('synthetic failure')", "failed"),
    ]:
        source = json.loads(draft.read_text(encoding="utf-8"))
        source["cells"][-1]["source"] = code
        draft.write_text(json.dumps(source), encoding="utf-8")
        record = notebooks.prepare(project["id"], project["revision"])
        assert (
            notebooks.execute({"project_id": project["id"], "id": record["id"]}, None)[
                "status"
            ]
            == status
        )
    native = run(recipe_template(recipe), runs_root=root, execute=True)
    assert native.status == "succeeded"
    view = project_evidence(root, project["id"])
    assert {row["pathway"] for row in view["attempts"]} == {"notebook", "native"}
    assert len(view["attempts"]) == 3
    assert any(
        row["status"] == "failed" and row["verified"] for row in view["attempts"]
    )
    selected = next(row for row in view["attempts"] if row["pathway"] == "native")
    request = {
        "expected_snapshot": view["snapshot_sha256"],
        "expected_head": None,
        "to_state": "candidate",
        "verdict": "supports",
        "attempt_ids": [selected["id"]],
        "actor": "local researcher",
        "reason": "fixture mechanics only",
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    reviewed = project_evidence(root, project["id"], review=request)
    assert reviewed["lifecycle"]["state"] == "candidate"
    assert not reviewed["lifecycle"]["reviews"][0]["needs_review"]
    result = subprocess.run(
        [
            python,
            "-I",
            "-X",
            "utf8",
            "-m",
            "quant_studio.project_evidence",
            "--runs-root",
            str(root),
            "--project",
            project["id"],
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout)["snapshot_sha256"] == view["snapshot_sha256"]
    (data / "prices.csv").write_text("date,value\n2026-01-01,99\n")
    changed = project_evidence(root, project["id"])
    assert changed["missing"] and changed["lifecycle"]["reviews"][0]["needs_review"]
    assert changed["lifecycle"]["reviews"][0]["reason"] == "fixture mechanics only"
    # Frozen notebooks are intact; current source drift does not rewrite their history.
    assert all(row["verified"] for row in changed["attempts"])
    assert "需要复核" in evidence_body(root, "token", project["id"])


def test_http_review_csrf_stale_form_and_failure_semantics(tmp_path, monkeypatch):
    configuration(tmp_path, monkeypatch)
    project = ProjectStore(tmp_path).save("<script>bad()</script>", "明确研究问题")
    server, thread = _start_http_server(tmp_path)
    host = f"127.0.0.1:{server.server_port}"
    route = f"/projects/{project['id']}/evidence"
    try:
        status, _, body = _request(server, "GET", route, host=host)
        assert status == 200 and "全部尝试与证据" in body.decode()
        assert "<script>bad()" not in body.decode()
        view = project_evidence(tmp_path, project["id"])
        fields = {
            "revision": project["revision"],
            "snapshot": view["snapshot_sha256"],
            "head": "",
            "state": "candidate",
            "verdict": "inconclusive",
            "attempt": "",
            "actor": "researcher",
            "reason": "尚无实验",
        }
        status, _, _ = _request(
            server,
            "POST",
            route + "-review",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 403
        fields["_csrf_token"] = "test-only-csrf-token"
        status, headers, _ = _request(
            server,
            "POST",
            route + "-review",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 303 and headers["Location"] == route
        status, _, _ = _request(
            server,
            "POST",
            route + "-review",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 400
        state = project_evidence(tmp_path, project["id"])["lifecycle"]
        assert len(state["reviews"]) == 1 and state["state"] == "candidate"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
