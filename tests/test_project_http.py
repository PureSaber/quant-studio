import json
import time

from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from tests.test_server import _request, _start_http_server


def test_browser_project_flow_and_csrf_boundaries(tmp_path):
    server, thread = _start_http_server(tmp_path)
    host = f"127.0.0.1:{server.server_port}"
    origin = f"http://{host}"
    token = "test-only-csrf-token"
    try:
        status, _, _ = _request(
            server,
            "POST",
            "/projects/new",
            host=host,
            origin=origin,
            fields={"name": "研究", "question": "波动率"},
        )
        assert status == 403
        status, headers, _ = _request(
            server,
            "POST",
            "/projects/new",
            host=host,
            origin=origin,
            fields={"name": "研究", "question": "波动率", "_csrf_token": token},
        )
        assert status == 303
        project_id = headers["Location"].split("/")[-1]
        project = ProjectStore(tmp_path).get(project_id)
        recipe = RecipeStore(tmp_path).save("合成流程", "synthetic-demo", {})
        project = ProjectStore(tmp_path).save(
            project["name"],
            project["question"],
            project_id=project_id,
            expected=project["revision"],
            recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
        )
        status, _, body = _request(server, "GET", headers["Location"], host=host)
        assert status == 200 and "研究助手" in body.decode()
        status, headers, _ = _request(
            server,
            "POST",
            f"/projects/{project_id}/run",
            host=host,
            origin=origin,
            fields={
                "revision": project["revision"],
                "recipe": recipe["id"] + "@" + recipe["revision"],
                "_csrf_token": token,
            },
        )
        assert status == 303
        manager = server.RequestHandlerClass.job_manager
        deadline = time.monotonic() + 10
        job_id = headers["Location"].split("/")[-1]
        while (
            manager.get(job_id)["status"] in {"queued", "running"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert manager.get(job_id)["status"] == "succeeded"
        assert ProjectStore(tmp_path).experiments(project_id)
        project = ProjectStore(tmp_path).save(
            project["name"],
            "新问题",
            project_id=project_id,
            expected=project["revision"],
            recipes=[],
        )
        assert ProjectStore(tmp_path).experiments(project_id)
        status, headers, _ = _request(
            server,
            "POST",
            f"/projects/{project_id}/assistant-prepare",
            host=host,
            origin=origin,
            fields={
                "revision": project["revision"],
                "question": "有哪些证据",
                "_csrf_token": token,
            },
        )
        assert status == 303
        status, _, body = _request(server, "GET", headers["Location"], host=host)
        assert status == 200 and "离线整理证据" in body.decode()
        requests = list(
            (tmp_path / ".projects" / project_id / "advice").glob("*/context.json")
        )
        assert json.loads(requests[0].read_text(encoding="utf-8"))["evidence"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
