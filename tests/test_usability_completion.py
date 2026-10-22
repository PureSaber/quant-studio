from datetime import datetime
from html.parser import HTMLParser

from quant_studio.presentation import display_time, recovery_body


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"details", "script", "style", "textarea"}:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def visible(html):
    parser = VisibleText()
    parser.feed(html)
    return " ".join(parser.text)


def test_unclassified_failure_has_recovery_action_without_exposing_traceback():
    page = recovery_body("Traceback: unexpected library failure")
    assert '<a class="btn"' in page
    assert "Traceback" not in visible(page)
    assert "Traceback" in page


def test_naive_saved_timestamp_is_readable_without_inventing_timezone():
    text = display_time("2026-10-21T12:01:02.123456")
    assert "123456" not in text and "时区未记录" in text
    aware = display_time("2026-10-21T12:01:02.123456+00:00")
    assert "123456" not in aware
    assert datetime.fromisoformat("2026-10-21T12:01:02+00:00").strftime("%Y") in aware


def test_durable_unix_timestamp_uses_the_shared_human_time_format():
    from datetime import UTC

    stamp = datetime(2026, 10, 21, 12, 1, 2, tzinfo=UTC)
    assert display_time(stamp.timestamp()) == display_time(stamp.isoformat())


def test_workbench_exposes_persistent_display_preference_without_posting_runtime_config(
    tmp_path,
):
    from quant_studio.server import _layout
    from quant_studio.setup import SettingsStore
    from quant_studio.setup_view import setup_body

    page = _layout(
        "设置", setup_body(SettingsStore(tmp_path / "settings.json"), "csrf"), "setup"
    )
    assert 'id="return-colors"' in page
    assert "红涨绿跌" in page and "绿涨红跌" in page
    assert "localStorage" in page
    assert "不构成投资建议" in page


def test_editor_rate_echo_and_unknown_numeric_parameter_keep_native_contract():
    from quant_studio.research_web import _fields, _read_fields

    data = {"commission": 0.0003, "unfamiliar_parameter": 2}
    page = _fields(data, "cfg:", human_units=True)
    assert "0.03%" in page
    assert "unfamiliar_parameter" not in visible(page)
    assert _read_fields(
        data,
        {"cfg:/commission": ["5"], "cfg:/unfamiliar_parameter": ["3"]},
        "cfg:",
        human_units=True,
    ) == {"commission": 0.0005, "unfamiliar_parameter": 3}


def test_navigation_aliases_lead_to_unified_records(tmp_path):
    from tests.test_server import _request, _start_http_server

    server, thread = _start_http_server(tmp_path)
    try:
        for old in ("/history", "/jobs", "/results", "/backtest"):
            status, headers, _ = _request(
                server, "GET", old, host=f"localhost:{server.server_port}"
            )
            assert status == 303
            assert headers["Location"].startswith("/experiments")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_plot_preserves_observations_and_only_draws_supplied_benchmark():
    import json
    import re
    from decimal import Decimal

    from quant_studio.nav import NavSeries
    from quant_studio.nav_explorer import explorer

    series = NavSeries(
        [("2020-01-01", Decimal("110")), ("2024-01-01", Decimal("99"))],
        initial_nav=Decimal("100"),
    )
    page = explorer(series)
    data = json.loads(re.search(r'id="nav-observations">(.*?)</script>', page, re.S)[1])
    assert data["baseline"] is None
    assert [Decimal(x) for x in data["values"]] == [v for _, v in series.plot_rows]
    assert Decimal(data["drawdowns"][-1]) == -10
    assert 'id="nav-log"' in page and "无基准" in page
    benchmark = NavSeries(
        [("2020-01-01", Decimal("100")), ("2024-01-01", Decimal("120"))],
        initial_nav=Decimal("100"),
    )
    page = explorer(series, baseline=benchmark)
    assert '"baseline": null' not in page and "有同口径基准" in page


def test_unified_index_keeps_failed_runs_history_and_jobs_searchable(tmp_path):
    from quant_studio.projects import ProjectStore
    from quant_studio.run_index import index_body
    from quant_studio.runner import run

    project = ProjectStore(tmp_path).save("测试研究", "测试问题", notes="保留这条备注")
    result = run("synthetic-demo", execute=True, runs_root=tmp_path)
    jobs = [
        {
            "job_id": "queued-one",
            "status": "queued",
            "template_id": "synthetic-demo",
            "created_at": "2026-10-21T12:00:00+00:00",
        }
    ]
    page = index_body(tmp_path, "token", {}, jobs)
    assert f"/experiments/{result.run_id}" in page and "/jobs/queued-one" in page
    assert f"/projects/{project['id']}" in page
    filtered = index_body(
        tmp_path, "token", {"kind": ["history"], "q": ["保留这条备注"]}, jobs
    )
    assert (
        f"/projects/{project['id']}" in filtered and "/jobs/queued-one" not in filtered
    )


def test_display_formatting_preserves_payload_and_collapses_technical_output():
    from quant_studio.display_html import display_html

    stamp = "2026-10-21T12:01:02.123456+00:00"
    page = display_html(
        f'<p>{stamp}</p><input value="{stamp}"><textarea>{stamp} unknown</textarea>'
        f"<pre>Traceback: {stamp}</pre><p>状态：unknown</p>"
    )
    assert (
        f'value="{stamp}"' in page and f"<textarea>{stamp} unknown</textarea>" in page
    )
    assert "123456" not in visible(page) and "Traceback" not in visible(page)
    assert "状态待确认" in visible(page)


def test_tool_scan_only_discovers_paths_and_preserves_manual_choice(tmp_path):
    from quant_studio.setup import discover_profile, empty_profile

    python = tmp_path / "quant-hk-equity" / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"not executable; discovery must never launch this")
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path)
    found = discover_profile(profile)
    assert found["python_by_repo"]["quant-hk-equity"] == str(python)
    assert not profile["python_by_repo"]
    manual = str(tmp_path / "manual-python.exe")
    profile["python_by_repo"]["quant-hk-equity"] = manual
    assert discover_profile(profile)["python_by_repo"]["quant-hk-equity"] == manual


def test_quick_trial_pins_template_parameters_to_a_project(tmp_path):
    from quant_studio.projects import ProjectStore
    from quant_studio.quick_trial import create_trial
    from quant_studio.recipes import RecipeStore

    recipe, project = create_trial(
        tmp_path, "synthetic-demo", {"initial_capital": 25000}
    )
    assert recipe["config"]["initial_capital"] == 25000
    assert ProjectStore(tmp_path).get(project["id"])["recipes"] == [
        {"id": recipe["id"], "revision": recipe["revision"]}
    ]
    assert RecipeStore(tmp_path).get(recipe["id"])["revision"] == recipe["revision"]
    assert "快速试跑" in project["name"]


def test_template_form_units_preserve_legacy_api_and_convert_browser_bp():
    from quant_studio.server import _parse_form_knobs

    assert _parse_form_knobs("a-share-four-factor", {"commission": ["0.0003"]}) == {
        "commission": 0.0003
    }
    assert _parse_form_knobs(
        "a-share-four-factor", {"_display_units": ["bp"], "commission": ["3"]}
    ) == {"commission": 0.0003}


def test_incomplete_notebook_batch_is_never_completed():
    from quant_studio.run_index import batch_status

    for status in ("cancelled", "interrupted", "cancel_failed", "blocked"):
        assert batch_status(["succeeded", status], 2) == status
    assert batch_status(["succeeded"], 2) == "incomplete"
    assert batch_status(["succeeded", "succeeded"], 2) == "completed"


def test_diff_uses_same_basis_points_as_editor():
    from quant_studio.workbench_web import diff_table

    page = diff_table(
        [{"path": "config.commission", "before": 0.0003, "after": 0.0005}]
    )
    assert "3.0000 bp = 0.03%" in page and "5.0000 bp = 0.05%" in page


def test_factor_labels_and_selections_round_trip_without_javascript():
    from quant_studio.research_web import _fields, _read_fields

    base = {"factors": ["pe_ratio", "pb_ratio"], "symbol": "00700"}
    page = _fields(
        base,
        "cfg:",
        factor_catalog=[
            {"name": "pe_ratio", "label": "市盈率"},
            {"name": "pb_ratio", "label": "市净率"},
        ],
    )
    assert "市盈率" in page and 'type="checkbox"' in page
    assert 'value="00700"' in page
    result = _read_fields(
        base, {"cfg:/factors": ["", "pb_ratio"], "cfg:/symbol": ["00700"]}, "cfg:"
    )
    assert result == {"factors": ["pb_ratio"], "symbol": "00700"}


def test_a_share_template_exposes_stock_pool_in_primary_summary():
    from quant_studio.research_web import _fields
    from quant_studio.templates import load_template, render_template

    template = load_template("a-share-four-factor")
    config = render_template(template).config
    page = _fields(config, "cfg:", template.metadata["knobs"], human_units=True)
    assert 'data-summary-value="沪深300"' in page
    assert 'name="cfg:/universe" value="hs300"' in page
    assert "股票池" in visible(page)


def test_unified_index_includes_native_notebook_and_remote_batches(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from quant_studio.notebook_batches import NotebookBatchStore
    from quant_studio.research_batches import BatchStore
    from quant_studio.run_index import index_body
    from tests.test_durable_batches import batch_state
    from tests.test_notebook_research import configure
    from tests.test_notebook_templates import saved_template
    from tests.test_research_batches import _recipe

    configure(tmp_path, monkeypatch)
    project, _, template = saved_template(tmp_path)
    notebook = NotebookBatchStore(tmp_path).prepare(
        project["id"],
        project["revision"],
        template["id"],
        {"window": [5, 10]},
        max_candidates=2,
    )
    recipe = _recipe(tmp_path)
    native = BatchStore(tmp_path).prepare(
        recipe["id"],
        recipe["revision"],
        {"seed": [1, 2]},
        max_candidates=2,
    )
    monkeypatch.setenv("QUANT_STUDIO_DURABLE_CONFIG", "configured-test-client")
    monkeypatch.setattr(
        "quant_studio.durable_research.configured_client",
        lambda: SimpleNamespace(
            list=lambda: [],
            batches=lambda: [batch_state()],
            supports_batches=lambda: True,
            history_limited=True,
        ),
    )
    page = index_body(tmp_path, "token", {"kind": ["batch"]})
    assert f"/batches/{native['batch_id']}" in page
    assert f"iterations?batch={notebook['id']}" in page
    assert "/durable-research/batches/" + "a" * 32 in page
    assert "升级后台研究工具后可读取完整历史" in page
    assert "&lt;script&gt;" not in visible(page)


def test_unified_index_sorts_timestamps_by_instant_not_iso_separator(tmp_path):
    from quant_studio.run_index import records

    rows = records(
        tmp_path,
        [
            {
                "job_id": "earlier",
                "status": "queued",
                "created_at": "2026-10-21T12:00:00+00:00",
            },
            {
                "job_id": "later",
                "status": "queued",
                "created_at": "2026-10-21 22:00:00+08:00",
            },
        ],
    )
    assert rows[0]["link"] == "/jobs/later?inspect=1"


def test_saved_run_keeps_frozen_project_link(tmp_path):
    from quant_studio.quick_trial import create_trial
    from tests.test_server import _request, _start_http_server

    recipe, project = create_trial(tmp_path, "synthetic-demo", {})
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    try:
        status, headers, body = _request(
            server,
            "POST",
            f"/research/{recipe['id']}",
            host=host,
            origin=f"http://{host}",
            fields={
                "_csrf_token": "test-only-csrf-token",
                "action": "execute_saved",
                "revision": recipe["revision"],
                "project_id": project["id"],
                "project_revision": project["revision"],
            },
        )
        assert status == 303, body
        job = server.RequestHandlerClass.job_manager.get(
            headers["Location"].split("/")[-1]
        )
        assert job["research_project"] == {
            "id": project["id"],
            "revision": project["revision"],
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
