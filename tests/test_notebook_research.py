import json
import os
import subprocess
import sys
import time

import pytest

from quant_studio import QuantStudioError
from quant_studio.notebooks import NotebookStore, notebook_config
from quant_studio.projects import ProjectStore


def configure(tmp_path, monkeypatch):
    path = tmp_path / "notebook-settings.json"
    path.write_text(
        json.dumps({"python": sys.executable, "lab_url": "http://127.0.0.1:8890/"})
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(path))


def test_notebook_submission_freezes_code_and_project_version(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    project = ProjectStore(tmp_path / "runs").save("探索", "数据分布如何")
    store = NotebookStore(tmp_path / "runs")
    draft = store.initialize(project["id"])
    saved = store.prepare(project["id"], project["revision"])
    draft.write_text("changed", encoding="utf-8")
    source = store.directory(project["id"], saved["id"]) / "source.ipynb"
    assert json.loads(source.read_text(encoding="utf-8"))["nbformat"] == 4
    assert saved["project_revision"] == project["revision"]
    assert project["id"] in store.lab_link(project["id"])
    with pytest.raises(QuantStudioError):
        store.prepare(project["id"], project["revision"])


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/?token=secret",
    ],
)
def test_lab_link_rejects_unsafe_or_credential_urls(tmp_path, monkeypatch, url):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"python": sys.executable, "lab_url": url}))
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(path))
    with pytest.raises(QuantStudioError):
        notebook_config()


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"),
    reason="independent notebook environment",
)
@pytest.mark.parametrize(
    "source, expected", [("print(6 * 7)", "succeeded"), ("1 / 0", "failed")]
)
def test_real_notebook_kernel_execution_and_failure_evidence(
    tmp_path, monkeypatch, source, expected
):
    path = tmp_path / "config.json"
    python = os.environ["QUANT_TEST_NOTEBOOK_PYTHON"]
    path.write_text(json.dumps({"python": python, "lab_url": "http://127.0.0.1:8890/"}))
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(path))
    project = ProjectStore(tmp_path / "runs").save("实际内核", "验证执行")
    store = NotebookStore(tmp_path / "runs")
    draft = store.initialize(project["id"])
    notebook = json.loads(draft.read_text(encoding="utf-8"))
    notebook["cells"][-1]["source"] = source
    draft.write_text(json.dumps(notebook), encoding="utf-8")
    prepared = store.prepare(project["id"], project["revision"])
    outcome = store.execute({"project_id": project["id"], "id": prepared["id"]}, None)
    assert outcome["status"] == expected
    directory = store.directory(project["id"], prepared["id"])
    environment = json.loads(
        (directory / "environment.json").read_text(encoding="utf-8")
    )
    assert os.path.normcase(environment["python"]) == os.path.normcase(python)
    record = store.get(project["id"], prepared["id"])
    assert {"report.html", "executed.ipynb", "environment.json"} <= record[
        "artifacts"
    ].keys()
    assert ("42" if expected == "succeeded" else "ZeroDivisionError") in (
        directory / "report.html"
    ).read_text(encoding="utf-8")
    assert "Parent appears to have exited" not in (
        directory / "execution.log"
    ).read_text(encoding="utf-8")


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"), reason="optional runtime"
)
def test_environment_drift_rejects_execution_before_user_code(tmp_path, monkeypatch):
    from quant_studio.datasets import file_hash

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "python": os.environ["QUANT_TEST_NOTEBOOK_PYTHON"],
                "lab_url": "http://127.0.0.1:8890/",
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(path))
    project = ProjectStore(tmp_path).save("环境", "不可静默切换依赖")
    store = NotebookStore(tmp_path)
    store.initialize(project["id"])
    record = store.prepare(project["id"], project["revision"])
    directory = store.directory(project["id"], record["id"])
    expected = directory / "requested-environment.json"
    value = json.loads(expected.read_text(encoding="utf-8"))
    value["packages"].append({"name": "prior-environment-only", "version": "1.0"})
    expected.write_text(json.dumps(value), encoding="utf-8")
    record["hashes"][expected.name] = file_hash(expected)
    store._save(directory, record)
    assert (
        store.execute({"project_id": project["id"], "id": record["id"]}, None)["status"]
        == "failed"
    )
    assert "changed after submission" in (directory / "execution.log").read_text(
        encoding="utf-8"
    )
    assert not (directory / "executed.ipynb").exists()


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"), reason="optional runtime"
)
def test_running_notebook_cancel_stops_kernel_and_retains_terminal_record(
    tmp_path, monkeypatch
):
    from quant_studio.jobs import JobManager
    from quant_studio.server import _job_executor
    from tests.test_jobs import _wait

    python = os.environ["QUANT_TEST_NOTEBOOK_PYTHON"]
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"python": python, "lab_url": "http://127.0.0.1:8890/"}))
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(path))
    project = ProjectStore(tmp_path).save("取消", "停止本次内核")
    store = NotebookStore(tmp_path)
    draft = store.initialize(project["id"])
    notebook = json.loads(draft.read_text(encoding="utf-8"))
    notebook["cells"][-1]["source"] = (
        "import os, time\nfrom pathlib import Path\n"
        "Path('kernel-pid.txt').write_text(str(os.getpid()))\ntime.sleep(30)"
    )
    draft.write_text(json.dumps(notebook), encoding="utf-8")
    record = store.prepare(project["id"], project["revision"])
    marker = store.directory(project["id"], record["id"]) / "kernel-pid.txt"
    manager = JobManager(tmp_path, execute=_job_executor(tmp_path))
    try:
        job = manager.submit(
            "notebook",
            "notebook",
            {},
            profile=None,
            notebook={"project_id": project["id"], "id": record["id"]},
        )
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert marker.exists(), manager.read_log(job["job_id"])
        pid = marker.read_text()
        manager.cancel(job["job_id"])
        assert (
            _wait(manager, job["job_id"], {"cancelled"}, timeout=10)["status"]
            == "cancelled"
        )
        assert store.get(project["id"], record["id"])["status"] == "cancelled"
        alive = "True"
        deadline = time.monotonic() + 5
        while alive == "True" and time.monotonic() < deadline:
            alive = subprocess.check_output(
                [
                    python,
                    "-I",
                    "-c",
                    "import psutil,sys; print(psutil.pid_exists(int(sys.argv[1])))",
                    pid,
                ],
                text=True,
            ).strip()
            if alive == "True":
                time.sleep(0.1)
        assert alive == "False", "cancelled kernel remained alive"
    finally:
        manager.close()
