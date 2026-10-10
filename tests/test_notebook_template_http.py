from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from tests.test_server import _request, _start_http_server


def test_project_passes_explicit_notebook_template_and_keeps_default(
    tmp_path, monkeypatch
):
    project = ProjectStore(tmp_path).save("模板研究", "数据能否用于这个问题？")
    calls = []
    monkeypatch.setattr(
        NotebookStore,
        "initialize",
        lambda self, identifier, revision=None, template=None: calls.append(
            (identifier, revision, template)
        ),
    )
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    try:
        status, _, body = _request(
            server, "GET", f"/projects/{project['id']}", host=host
        )
        assert status == 200
        assert b"data-quality-exploration" in body
        fields = {
            "_csrf_token": "test-only-csrf-token",
            "revision": project["revision"],
            "template": "data-quality-exploration",
        }
        status, _, _ = _request(
            server,
            "POST",
            f"/projects/{project['id']}/notebook-init",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 303
        assert calls[-1] == (
            project["id"],
            project["revision"],
            "data-quality-exploration",
        )
        fields.pop("template")
        assert (
            _request(
                server,
                "POST",
                f"/projects/{project['id']}/notebook-init",
                host=host,
                origin=f"http://{host}",
                fields=fields,
            )[0]
            == 303
        )
        assert calls[-1][2] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
