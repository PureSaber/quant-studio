import json
import os
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.notebook_metrics import compare_notebooks, comparison_csv
from quant_studio.notebook_templates import NotebookTemplateStore
from quant_studio.notebooks import NotebookStore
from tests.test_notebook_metrics import metric
from tests.test_notebook_research import configure
from tests.test_notebook_templates import PARAMETERS, saved_template
from tests.test_server import _request, _start_http_server


def test_template_save_form_reuse_and_comparison_routes(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    project, _, _ = saved_template(tmp_path)
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"

    def post(path, **fields):
        return _request(
            server,
            "POST",
            path,
            host=host,
            origin=f"http://{host}",
            fields={"_csrf_token": "test-only-csrf-token", **fields},
        )

    try:
        status, headers, _ = post(
            f"/projects/{project['id']}/template-save",
            revision=project["revision"],
            name="<样本模板>",
            parameters=json.dumps(PARAMETERS),
        )
        assert status == 303
        location = headers["Location"]
        template_id = parse_qs(urlparse(location).query)["template"][0]
        template = NotebookTemplateStore(tmp_path).get(template_id)
        status, _, body = _request(server, "GET", location, host=host)
        assert status == 200 and b"param_window" in body and b"&lt;" in body
        before = len(list((tmp_path / ".projects").iterdir()))
        status, _, _ = post(
            "/projects/templates/create",
            template=template_id,
            revision=template["revision"],
            name="新研究",
            param_window="0",
        )
        assert status == 400
        assert len(list((tmp_path / ".projects").iterdir())) == before
        status, headers, _ = post(
            "/projects/templates/create",
            template=template_id,
            revision=template["revision"],
            name="新研究",
            param_window="5",
            param_label="新样本",
        )
        assert status == 303
        status, _, body = _request(server, "GET", headers["Location"], host=host)
        assert status == 200 and "新样本" in body.decode()
        status, _, body = _request(
            server, "GET", "/projects/notebook-compare", host=host
        )
        assert status == 200 and "选择2至4个实验" in body.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def completed_record(root, project, metrics, *, status="succeeded"):
    store = NotebookStore(root)
    record = store.prepare(project["id"], project["revision"])
    directory = store.directory(project["id"], record["id"])
    path = directory / "metrics.json"
    path.write_text(json.dumps(metrics), encoding="utf-8")
    record.update(status=status, artifacts={"metrics.json": file_hash(path)})
    store._save(directory, record)
    return project["id"] + ":" + record["id"]


def test_comparison_preserves_conditions_units_missing_failure_and_integrity(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch)
    project, _, template = saved_template(tmp_path)
    clone = NotebookTemplateStore(tmp_path).instantiate(
        template["id"], template["revision"], "=1+2", [], {"window": 5}
    )
    first = completed_record(
        tmp_path,
        project,
        {"样本数": metric(0), "相关系数": metric(None, reason="有效样本不足")},
    )
    second = completed_record(tmp_path, clone, {"样本数": metric(0.5, unit="比例")})
    failed = completed_record(
        tmp_path, project, {"样本数": metric(999)}, status="failed"
    )
    result = compare_notebooks(tmp_path, [first, second, failed])
    assert any("统计口径" in w for w in result["warnings"])
    assert any("参数不同" in w for w in result["warnings"])
    assert len(result["rows"]) == 3
    assert any(0 in r and "未成功，不采用" in r for r in result["rows"])
    csv = comparison_csv(result).decode("utf-8-sig")
    assert "'=1+2" in csv and ",999" not in csv and "有效样本不足" in csv
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    try:
        query = urlencode({"run": [first, second]}, doseq=True)
        status, _, body = _request(
            server, "GET", "/projects/notebook-compare?" + query, host=host
        )
        assert status == 200 and "指标" in body.decode()
        status, headers, body = _request(
            server,
            "GET",
            "/projects/notebook-compare?" + query + "&download=csv",
            host=host,
        )
        assert status == 200 and "text/csv" in headers["Content-Type"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    run_id = second.split(":")[1]
    artifact = NotebookStore(tmp_path).directory(clone["id"], run_id) / "metrics.json"
    artifact.write_text("{}", encoding="utf-8")
    with pytest.raises(QuantStudioError, match="完整性"):
        compare_notebooks(tmp_path, [first, second])


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"),
    reason="requires optional Notebook environment",
)
def test_real_parameterized_notebooks_export_known_metrics(tmp_path, monkeypatch):
    config = tmp_path / "notebook-config.json"
    config.write_text(
        json.dumps(
            {
                "python": os.environ["QUANT_TEST_NOTEBOOK_PYTHON"],
                "lab_url": "http://127.0.0.1:8890/",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(config))
    project, draft, _ = saved_template(tmp_path)
    notebook = json.loads(draft.read_text(encoding="utf-8"))
    notebook["cells"][-1]["source"] = (
        "samples = [2, 4, 8, 16, 32]\n"
        "selected = samples[:parameters['window']]\n"
        "research_metrics = {\n"
        "    '样本数': {'value': len(selected), 'unit': '个',\n"
        "        'definition': '窗口内合成样本数'},\n"
        "    '样本均值': {'value': sum(selected) / len(selected), 'unit': '合成单位',\n"
        "        'definition': '窗口内合成数列的算术均值'}\n"
        "}\nprint(research_metrics, parameters['label'])"
    )
    draft.write_text(json.dumps(notebook), encoding="utf-8")
    templates = NotebookTemplateStore(tmp_path)
    template = templates.save(
        project["id"], project["revision"], "合成窗口实验", PARAMETERS
    )
    references = []
    store = NotebookStore(tmp_path)
    for window in (2, 4):
        clone = templates.instantiate(
            template["id"],
            template["revision"],
            f"窗口{window}",
            [],
            {"window": window, "label": "');raise Exception('injected')#"},
        )
        record = store.prepare(clone["id"], clone["revision"])
        outcome = store.execute({"project_id": clone["id"], "id": record["id"]}, None)
        assert outcome["status"] == "succeeded", outcome
        references.append(clone["id"] + ":" + record["id"])
    result = compare_notebooks(tmp_path, references)
    assert result["records"][0]["metrics"]["样本均值"]["value"] == 3
    assert result["records"][1]["metrics"]["样本均值"]["value"] == 7.5
    assert result["records"][0]["code"] == result["records"][1]["code"]
    assert not (tmp_path / "injected").exists()
