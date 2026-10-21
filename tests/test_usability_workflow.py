from decimal import Decimal

from quant_studio.intake_web import _retry_body
from quant_studio.nav import NavSeries
from quant_studio.presentation import metric_cards, progress_steps, status_label
from quant_studio.project_web import project_body
from quant_studio.projects import ProjectStore
from quant_studio.research_web import _fields, _read_fields, editor_body


def test_statuses_keep_unconfirmed_outcomes_distinct():
    assert status_label("incomplete") != status_label("failed")
    assert "待确认" in status_label("incomplete")
    assert "未确认" in status_label("cancel_failed")
    assert status_label("cancel_failed") != status_label("cancelled")


def test_progress_does_not_invent_ledger_verification():
    assert "核对账本" not in progress_steps({"action": "workflow"}, {})
    assert "核对账本" in progress_steps(
        {"action": "workflow"}, {"verification_argv": ["verify"]}
    )
    assert "运行策略" not in progress_steps({"action": "check"}, {})


def test_metrics_do_not_annualize_undeclared_event_or_synthetic_data():
    series = NavSeries([("2026-01-02", Decimal("101"))], initial_nav=Decimal("100"))
    page = metric_cards(series, {}, synthetic=True)
    assert "+1.00%" in page and "不适用" in page
    page = metric_cards(series, {})
    assert "不可计算" in page and "252" not in page


def test_rate_input_uses_basis_points_without_changing_native_units():
    page = _fields({"commission": 0.0003}, "cfg:", human_units=True)
    assert "bp" in page and 'value="3' in page
    assert (
        _read_fields(
            {"commission": 0.0003}, {"cfg:/commission": ["5"]}, "cfg:", human_units=True
        )["commission"]
        == 0.0005
    )


def test_editor_runs_submitted_fields_instead_of_a_separate_saved_form():
    page = editor_body("synthetic-demo", "test-token")
    assert 'value="save_run"' in page
    assert "检查数据并运行" in page
    assert "execute_saved" not in page


def test_project_conclusions_and_notes_exist_before_any_run(tmp_path):
    project = ProjectStore(tmp_path).save("Still uncertain", "Why?")
    page = project_body(tmp_path, "test-token", project["id"], {"tab": ["conclusions"]})
    assert 'name="notes"' in page and "保存备注" in page
    assert 'name="rationale"' in page
    assert "先完成至少一次运行" not in page
    assert "交互研究与Notebook实验" not in page
    overview = project_body(tmp_path, "test-token", project["id"])
    assert "方案与运行" in overview and "结论" in overview
    assert 'name="rationale"' not in overview


def test_missing_import_tool_does_not_suggest_encoding_changes():
    page = _retry_body(
        {"filename": "prices.csv", "sha256": "a" * 64},
        "upload-id",
        "utf-8",
        ",",
        "请在环境配置中连接quant-data-kit的独立Python和源码",
    )
    assert "连接数据检查工具" in page and 'href="/setup"' in page
    assert "调整编码或分隔符后重试" not in page


def test_create_project_save_notes_and_run_current_parameters(tmp_path):
    import json

    from quant_studio.recipes import RecipeStore
    from quant_studio.research_web import _leaves
    from tests.test_server import _request, _start_http_server

    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"

    def post(path, fields):
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
            "/projects/new",
            {
                "name": "Current parameters",
                "question": "Does size matter?",
                "template_id": "synthetic-demo",
            },
        )
        assert status == 303 and headers["Location"].startswith("/research/")
        projects, recipes = ProjectStore(tmp_path), RecipeStore(tmp_path)
        project = projects.list()[0]
        first = recipes.get(project["recipes"][0]["id"])
        assert len(recipes.history(first["id"])) == 1
        status, _, _ = post(
            f"/projects/{project['id']}/notes-save",
            {
                "revision": project["revision"],
                "notes": "Insufficient evidence; pause idea.",
            },
        )
        assert status == 303
        project = projects.get(project["id"])
        assert project["notes"] == "Insufficient evidence; pause idea."
        assert project["recipes"] == [
            {"id": first["id"], "revision": first["revision"]}
        ]
        config = {**first["config"], "initial_capital": 76543}
        fields = {
            "action": "save_run",
            "revision": first["revision"],
            "ui_units": "bp",
            "template_id": first["template_id"],
            "name": first["name"],
            "project_id": project["id"],
            "project_revision": project["revision"],
        }
        for prefix, value in (("cfg:", config), ("cli:", first["cli"])):
            fields.update(
                {
                    prefix + path: json.dumps(value)
                    if isinstance(value, (bool, dict, list)) or value is None
                    else str(value)
                    for path, _, value in _leaves(value)
                }
            )
        status, headers, _ = post(f"/research/{first['id']}", fields)
        assert status == 303 and headers["Location"].startswith("/jobs/")
        manager = server.RequestHandlerClass.job_manager
        job = manager.list()[0]
        current = recipes.get(first["id"])
        assert job["action"] == "workflow"
        assert job["recipe"]["config"]["initial_capital"] == 76543
        assert job["recipe"]["revision"] == current["revision"] != first["revision"]
        assert recipes.get(first["id"], first["revision"])["config"] == first["config"]
        assert (
            job["research_project"]["revision"]
            == projects.get(project["id"])["revision"]
        )
        status, _, _ = post(f"/research/{first['id']}", fields)
        assert status == 400 and len(manager.list()) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failure_comparison_preserves_config_and_diagnostics(tmp_path):
    import json

    from quant_studio.runner import preview
    from quant_studio.templates import load_template
    from quant_studio.workbench_web import compare_body

    records = []
    for capital, message in ((10000, "Missing input A"), (20000, "Missing input B")):
        result = preview(
            load_template("synthetic-demo"),
            {"initial_capital": capital},
            runs_root=tmp_path,
        )
        path = result.run_dir / "result.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value.update(status="failed", message=message)
        path.write_text(json.dumps(value), encoding="utf-8")
        records.append(result.run_id)
    page = compare_body(tmp_path, records)
    assert "Missing input A" in page and "Missing input B" in page
    assert "10000" in page and "20000" in page
    assert "配置和失败原因仍可比较" in page


def test_one_observation_without_opening_has_no_invented_return():
    page = metric_cards(NavSeries([("2026-01-02", Decimal("101"))]), {})
    assert "不可计算" in page and "+0.00%" not in page


def test_mixed_comparison_omits_only_unavailable_curve(tmp_path):
    import json
    import re

    from quant_studio.runner import preview, run
    from quant_studio.templates import load_template
    from quant_studio.workbench_web import compare_body

    first = run("synthetic-demo", execute=True, runs_root=tmp_path)
    second = run("synthetic-demo", execute=True, runs_root=tmp_path)
    failed = preview(load_template("synthetic-demo"), runs_root=tmp_path)
    page = compare_body(tmp_path, [first.run_id, second.run_id, failed.run_id])
    data = json.loads(re.search(r'id="curve-data">(.*?)</script>', page, re.S)[1])
    assert {row["run_id"] for row in data["series"]} == {first.run_id, second.run_id}
    assert f"/experiments/{failed.run_id}" in page
    assert "参数差异" in page


def test_template_project_keeps_runtime_parameters_and_rejects_empty_question(tmp_path):
    import pytest

    from quant_studio import QuantStudioError
    from quant_studio.project_web import ProjectHandler
    from quant_studio.recipes import RecipeStore

    class Handler(ProjectHandler):
        runs_root = tmp_path

        def _redirect(self, url):
            self.destination = url

    handler = Handler()
    fields = {
        "name": ["Research"],
        "question": [""],
        "template_id": ["a-share-four-factor"],
    }
    with pytest.raises(QuantStudioError):
        handler._project_post("/projects/new", {**fields})
    assert not RecipeStore(tmp_path).list()
    handler._project_post("/projects/new", {**fields, "question": ["Universe size?"]})
    recipe = RecipeStore(tmp_path).list()[0]
    assert recipe["cli"] == {"symbols_limit": 50}
    assert 'name="cli:/symbols_limit"' in editor_body(
        "a-share-four-factor", "token", recipe
    )
