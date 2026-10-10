"""Optional tests exercise real Lab, kernel, Parquet and the separate agent process."""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from quant_studio.data_catalog import preview_file
from quant_studio.datasets import DatasetStore
from quant_studio.notebooks import NotebookStore
from quant_studio.project_assistant import AssistantStore
from quant_studio.projects import ProjectStore
from quant_studio.runner import _terminate_process_scope
from tests.test_server import _request, _start_http_server


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"), reason="optional notebook runtime"
)
def test_real_lab_save_submit_plot_download_and_parquet(tmp_path, monkeypatch):
    python = os.environ["QUANT_TEST_NOTEBOOK_PYTHON"]
    root, source = tmp_path / "runs", tmp_path / "data"
    source.mkdir()
    subprocess.run(
        [
            python,
            "-I",
            "-c",
            "import pyarrow as p, pyarrow.parquet as q, sys; "
            "q.write_table(p.table({'symbol':['00700','00700'],'value':[42,None]}),sys.argv[1])",
            str(source / "bars.parquet"),
        ],
        check=True,
    )
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "provider": "synthetic",
                "column_descriptions": {"bars.parquet": {"value": "演示值，不是行情"}},
            }
        ),
        encoding="utf-8",
    )
    dataset = DatasetStore(root).register("演示", "a-share-four-factor", str(source))
    runtimes = tmp_path / "runtimes.json"
    runtimes.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {"a-share-multifactor": python},
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_RUNTIMES", str(runtimes))
    preview = preview_file(root, dataset["id"], "bars.parquet")
    assert preview["columns"][1]["missing"] == 1
    assert preview["columns"][1]["description"] == "演示值，不是行情"
    assert preview["rows"][0][0] == "00700"
    project = ProjectStore(root).save(
        "Lab研究", "读取数据并绘图", datasets=[dataset["id"]]
    )
    NotebookStore(root).initialize(project["id"])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "notebooks.json"
    config.write_text(
        json.dumps({"python": python, "lab_url": f"http://127.0.0.1:{port}"})
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(config))
    secret = secrets.token_urlsafe(32)
    token = tmp_path / "jupyter-token.txt"
    token.write_text(secret)

    def api(path, method="GET", content=None, authenticated=True):
        headers = {"Authorization": f"token {secret}"} if authenticated else {}
        if content is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"http://127.0.0.1:{port}" + path,
            method=method,
            headers=headers,
            data=json.dumps(content).encode() if content is not None else None,
        )
        with urlopen(request, timeout=5) as response:
            return response.read()

    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    with (tmp_path / "lab.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "quant_studio.notebook_lab",
                "--runs-root",
                str(root),
                "--token-file",
                str(token),
                "--port",
                str(port),
            ],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        server, thread = _start_http_server(root)
        try:
            deadline = time.monotonic() + 35
            while True:
                try:
                    api("/api")
                    break
                except URLError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise AssertionError(
                            "JupyterLab did not become ready"
                        ) from None
                    time.sleep(0.2)
            with pytest.raises(HTTPError) as denied:
                api("/api/contents", authenticated=False)
            assert denied.value.code == 403
            assert b"jupyter" in api("/lab").lower()
            kernel = json.loads(api("/api/kernels", "POST", {"name": "python3"}))
            assert kernel["name"] == "python3"
            api("/api/kernels/" + kernel["id"], "DELETE")
            path = f"/api/contents/{project['id']}/research.ipynb"
            model = json.loads(api(path))
            model["content"]["cells"][-1]["source"] = (
                "import pandas as pd\nimport matplotlib.pyplot as plt\n"
                "item = context['datasets'][0]\n"
                "df = pd.read_parquet(Path(item['path']) / 'bars.parquet')\n"
                "print(df.iloc[0]['symbol'], df.iloc[0]['value'])\n"
                "plt.plot([1, 2, 3], [2, 4, 3]); plt.show()\n"
            )
            api(path, "PUT", {"type": "notebook", "content": model["content"]})
            host = f"127.0.0.1:{server.server_port}"
            status, headers, _ = _request(
                server,
                "POST",
                f"/projects/{project['id']}/notebook-run",
                host=host,
                origin=f"http://{host}",
                fields={
                    "revision": project["revision"],
                    "_csrf_token": "test-only-csrf-token",
                },
            )
            assert status == 303
            manager = server.RequestHandlerClass.job_manager
            job_id = headers["Location"].split("/")[-1]
            deadline = time.monotonic() + 40
            while (
                manager.get(job_id)["status"] in {"queued", "running"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            assert manager.get(job_id)["status"] == "succeeded", manager.read_log(
                job_id
            )
            run = NotebookStore(root).list(project["id"])[0]
            route = f"/projects/{project['id']}/notebook/{run['id']}"
            status, _, body = _request(server, "GET", route, host=host)
            assert (
                status == 200
                and b"00700 42.0" in body
                and b"data:image/png;base64" in body
            )
            status, headers, body = _request(
                server, "GET", route + "/executed.ipynb", host=host
            )
            assert status == 200 and headers["Content-Disposition"].startswith(
                "attachment"
            )
            assert json.loads(body)["cells"][-1]["execution_count"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            try:
                api("/api/shutdown", "POST", {})
                process.wait(timeout=15)
            except (URLError, subprocess.TimeoutExpired):
                _terminate_process_scope(process, force=True)
                process.wait(timeout=5)


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_AGENT_SOURCE"), reason="separate agent checkout"
)
def test_real_agent_offline_subprocess(tmp_path, monkeypatch):
    python = os.environ["QUANT_TEST_AGENT_PYTHON"]
    monkeypatch.setenv(
        "PYTHONPATH", str(Path(os.environ["QUANT_TEST_AGENT_SOURCE"]) / "src")
    )
    runtimes = tmp_path / "runtimes.json"
    runtimes.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {"quant-agent": python},
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_RUNTIMES", str(runtimes))
    monkeypatch.delenv("QUANT_AGENT_LLM_OK", raising=False)
    project = ProjectStore(tmp_path).save("助手", "需要哪些输入")
    store = AssistantStore(tmp_path)
    record = store.prepare(project["id"], project["revision"], "梳理已有证据")
    result = store.execute({"project_id": project["id"], "id": record["id"]}, None)
    assert result["status"] == "succeeded"
    answer = json.loads(
        (store.directory(project["id"], record["id"]) / "answer.json").read_text(
            encoding="utf-8"
        )
    )
    assert not answer["model_invoked"] and answer["provider"] == "none"
    assert answer["findings"][0]["citations"] == ["E1"]
