import json
import os
import subprocess
import sys
import time

import pytest

from quant_studio import QuantStudioError
from quant_studio.notebooks import NotebookStore
from quant_studio.project_web import notebook_result_body
from quant_studio.projects import ProjectStore

iso = pytest.importorskip("quant_pipeline.isolation")


def configure(tmp_path, monkeypatch, source="print(42)", **policy):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "python": sys.executable,
                "lab_url": "http://127.0.0.1:8890/",
                "timeout_seconds": 30,
                "isolation": {
                    "schema": "quant.research-isolation/v1",
                    "backend": "docker-linux",
                    "image": os.environ.get(
                        "QUANT_NOTEBOOK_IMAGE", "sha256:" + "1" * 64
                    ),
                    "memory_mb": 512,
                    "pids": 128,
                    "timeout_seconds": 60,
                    "seed": 17,
                    **policy,
                },
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(config))
    project = ProjectStore(tmp_path).save("隔离实验", "验证环境边界")
    store = NotebookStore(tmp_path)
    draft = store.initialize(project["id"])
    notebook = json.loads(draft.read_text("utf-8"))
    notebook["cells"][-1]["source"] = source
    draft.write_text(json.dumps(notebook), "utf-8")
    return project, store


def test_unsupported_host_is_visible_and_never_falls_back(tmp_path, monkeypatch):
    project, store = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(iso.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("host fallback called")
    )
    with pytest.raises(QuantStudioError, match="Linux host"):
        store.prepare(project["id"], project["revision"])
    record = store.list(project["id"])[0]
    assert record["status"] == "blocked"
    body = notebook_result_body(tmp_path, project["id"], record["id"])
    assert "Linux Docker" in body and "Linux host" in body


def test_invalid_isolation_is_not_treated_as_local_mode(tmp_path, monkeypatch):
    project, store = configure(tmp_path, monkeypatch, backend="subprocess")
    with pytest.raises(QuantStudioError, match="enforced backend"):
        store.prepare(project["id"], project["revision"])


@pytest.mark.skipif(
    not os.environ.get("QUANT_NOTEBOOK_IMAGE"), reason="real Linux Notebook image"
)
@pytest.mark.parametrize(
    "source,expected",
    [
        (
            """import os, socket, random
from pathlib import Path
assert 'PRIVATE_API_KEY' not in os.environ
assert random.randint(0, 100) == 66
try:
    Path('inputs.json').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('input writable')
s = socket.socket(); s.settimeout(1)
try:
    s.connect(('1.1.1.1', 53))
except OSError:
    pass
else:
    raise AssertionError('network escape')
research.metric('answer', 42, unit='count', definition='6 times 7')
print('isolated-notebook-42')
""",
            "succeeded",
        ),
        ("raise ValueError('intentional-notebook-failure')", "failed"),
    ],
)
def test_actual_notebook_execution_and_error_evidence(
    tmp_path, monkeypatch, source, expected
):
    monkeypatch.setenv("PRIVATE_API_KEY", "private-sentinel")
    project, store = configure(tmp_path, monkeypatch, source)
    record = store.prepare(project["id"], project["revision"])
    result = store.execute({"project_id": project["id"], "id": record["id"]}, None)
    assert result["status"] == expected, result
    saved = store.get(project["id"], record["id"])
    assert "isolation.json" in saved["artifacts"]
    manifest = json.loads(
        store.artifact(project["id"], record["id"], "isolation.json").read_text()
    )
    assert (
        manifest["evidence_kind"] == "container-execution"
        and manifest["cleanup_verified"]
    )
    assert manifest["seed"] == 17
    assert manifest["environment"]["image_id"] == os.environ["QUANT_NOTEBOOK_IMAGE"]
    store.verify_inputs(saved)
    if expected == "succeeded":
        assert "metrics.json" in saved["artifacts"]
        assert (
            "isolated-notebook-42"
            in store.artifact(project["id"], record["id"], "report.html").read_text()
        )
    else:
        assert (
            "intentional-notebook-failure"
            in store.artifact(project["id"], record["id"], "report.html").read_text()
        )
    environment = json.loads(
        store.artifact(project["id"], record["id"], "environment.json").read_text()
    )
    assert any(package["name"] == "nbclient" for package in environment["packages"])


@pytest.mark.skipif(
    not os.environ.get("QUANT_NOTEBOOK_IMAGE"), reason="real Linux Notebook image"
)
def test_job_manager_cancel_removes_kernel_and_container(tmp_path, monkeypatch):
    from quant_studio.jobs import JobManager
    from quant_studio.server import _job_executor

    project, store = configure(
        tmp_path,
        monkeypatch,
        "import subprocess,time\nsubprocess.Popen(['sleep','70'])\ntime.sleep(70)",
    )
    record = store.prepare(project["id"], project["revision"])
    manager = JobManager(tmp_path, execute=_job_executor(tmp_path))
    try:
        job = manager.submit(
            "notebook",
            "notebook",
            {},
            profile=None,
            notebook={"project_id": project["id"], "id": record["id"]},
        )
        deadline, container = time.monotonic() + 30, None
        while time.monotonic() < deadline:
            ids = subprocess.check_output(
                ["docker", "ps", "-q", "--filter", "label=quant.research-isolation=v1"],
                text=True,
            ).split()
            if ids:
                top = subprocess.run(
                    ["docker", "top", ids[0]],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if "sleep 70" in top.stdout:
                    container = ids[0]
                    break
            time.sleep(0.1)
        assert container is not None, manager.get(job["job_id"])
        manager.cancel(job["job_id"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and manager.get(job["job_id"])[
            "status"
        ] not in {"cancelled", "cancel_failed"}:
            time.sleep(0.1)
        assert manager.get(job["job_id"])["status"] == "cancelled"
        saved = store.get(project["id"], record["id"])
        assert saved["status"] == "cancelled"
        manifest = json.loads(
            store.artifact(project["id"], record["id"], "isolation.json").read_text()
        )
        assert (
            manifest["status"] == "cancelled" and manifest["cleanup_verified"] is True
        )
        assert not subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "id=" + container], text=True
        ).strip()
    finally:
        manager.close()
