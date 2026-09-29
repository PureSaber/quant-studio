import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.__main__ import main
from quant_studio.runner import preview, run
from quant_studio.server import (
    compiled_from_form,
    render_backtest,
    render_data,
    render_home,
    render_results,
    render_run,
    render_template_page,
    resolve_run_asset,
    validate_host,
)


def test_home_has_three_research_cards_and_one_synthetic_card():
    page = render_home()

    assert page.count('class="template-card research"') == 3
    assert page.count('class="template-card synthetic"') == 1
    assert "A 股四类因子研究" in page
    assert "港股日频研究" in page
    assert "模拟盘步进研究" in page
    assert "合成净值样例" in page


def test_template_page_contains_knob_form():
    page = render_template_page("hk-equity-daily")

    assert "<form" in page
    assert 'name="initial_cash"' in page
    assert 'name="rebalance_sessions"' in page


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10"])
def test_non_loopback_host_is_rejected(host):
    with pytest.raises(QuantStudioError, match="host"):
        validate_host(host)


def test_preview_page_shows_argv_without_nav_chart(tmp_path):
    result = preview(
        "a-share-four-factor",
        {"symbols_limit": 20},
        runs_root=tmp_path,
    )

    page = render_run(result.run_dir)

    assert "a_share_multifactor.backtest" in page
    assert "--symbols-limit" in page
    assert "生成的配置" in page
    assert "<svg" not in page
    assert "<iframe" not in page


def test_synthetic_result_embeds_report_and_shows_disclaimer(tmp_path):
    result = run("synthetic-demo", {}, execute=True, runs_root=tmp_path)

    page = render_run(result.run_dir)

    assert "合成样例，不是市场收益" in page
    assert f"/runs/{result.run_id}/files/report.html" in page


def test_only_html_and_csv_inside_run_directory_can_be_served(tmp_path):
    run_dir = tmp_path / "safe-run"
    run_dir.mkdir()
    (run_dir / "report.html").write_text("ok", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps({}), encoding="utf-8")

    assert resolve_run_asset(tmp_path, "safe-run", "report.html").is_file()
    with pytest.raises(QuantStudioError):
        resolve_run_asset(tmp_path, "safe-run", "../outside.html")
    with pytest.raises(QuantStudioError):
        resolve_run_asset(tmp_path, "safe-run", "result.json")


def test_cli_preview_parses_declared_knob_types(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_run(template_id, knobs, **kwargs):
        captured.update(template_id=template_id, knobs=knobs, kwargs=kwargs)
        return preview(template_id, knobs, runs_root=tmp_path)

    monkeypatch.setattr("quant_studio.__main__.run", fake_run)

    assert (
        main(
            [
                "preview",
                "a-share-four-factor",
                "--set",
                "symbols_limit=8",
                "--set",
                "rebalance_freq=weekly",
            ]
        )
        == 0
    )
    assert captured["knobs"] == {"symbols_limit": 8, "rebalance_freq": "weekly"}
    assert captured["kwargs"]["execute"] is False
    assert '"status": "previewed"' in capsys.readouterr().out


def test_missing_workspace_is_preview_only(monkeypatch):
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)

    home = render_home()
    form = render_template_page("a-share-four-factor")

    assert "可运行" in home
    assert "a-share-multifactor 只能预览" in home
    assert 'value="execute" disabled' in form


def test_debug_preset_fills_declared_fields(monkeypatch):
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)

    page = render_template_page("a-share-four-factor", "debug")

    assert "调试" in page
    assert 'value="weekly" selected' in page
    assert 'name="symbols_limit" value="10"' in page
    assert 'name="initial_capital" value="10000"' in page


def test_imported_nav_is_charted_with_drawdown(tmp_path, monkeypatch):
    from tests.test_runner import test_success_copies_upstream_nav_and_report

    test_success_copies_upstream_nav_and_report(tmp_path, monkeypatch)
    run_dir = next((tmp_path / "runs").iterdir())
    page = render_run(run_dir)

    assert "<svg" in page
    assert "最大回撤" in page
    assert "期末净值" in page
    assert "0.90" in page
    assert "持仓" in page
    assert "回撤" in page
    assert "未提供基准净值" in page


def test_data_page_sees_local_snapshot_and_fetch_command(monkeypatch, tmp_path):
    folder = tmp_path / "a-share-multifactor" / "data"
    folder.mkdir(parents=True)
    (folder / "prices.csv").write_text("date,close\n", encoding="utf-8")
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))

    page = render_data()

    assert "1 个数据文件" in page
    assert "最近更新" in page
    assert 'href="/environment"' in page
    assert "a_share_multifactor.fetch_data" in page


def test_template_flows_compile_declared_config():
    share = render_template_page("a-share-four-factor", "debug")
    hong_kong = render_template_page("hk-equity-daily")
    paper = render_template_page("paper-sim")
    synthetic = render_template_page("synthetic-demo")

    assert 'id="factors"' in share
    assert "rebalance_freq: weekly" in share
    assert "--symbols-limit\n10" in share
    assert 'id="factors"' not in hong_kong
    assert "quant-hk" in hong_kong
    assert "quant-paper" in paper
    assert "固定收益率" in synthetic
    assert "进程内合成样例，无外部命令" in synthetic
    for page in (share, hong_kong, paper, synthetic):
        assert 'id="data"' in page
        assert 'id="trade"' in page
        assert 'id="run"' in page
        assert 'id="code"' in page
        assert "手写代码" in page


def test_compiled_form_keeps_only_submitted_factors():
    text = compiled_from_form(
        "a-share-four-factor",
        {
            "factor_form": ["1"],
            "factor": ["pe_ratio"],
            "rebalance_freq": ["weekly"],
            "initial_capital": ["20000"],
            "symbols_limit": ["12"],
            "commission": ["0.0003"],
            "slippage": ["0.001"],
        },
    )

    assert "pe_ratio" in text
    assert "momentum_20d" not in text
    assert "--symbols-limit\n12" in text
    assert "<本次运行>/config.yaml" in text


def test_backtest_lists_previews_and_results_keep_finished_runs(tmp_path):
    previewed = preview(
        "a-share-four-factor",
        {"symbols_limit": 20},
        runs_root=tmp_path,
    )
    finished = run("synthetic-demo", {}, execute=True, runs_root=tmp_path)

    backtest = render_backtest(tmp_path)
    results = render_results(tmp_path)
    detail = render_run(previewed.run_dir)
    assert "已完成" in render_run(finished.run_dir)

    assert backtest.count("synthetic-demo") == 1
    assert "a-share-four-factor" in backtest
    assert "仅预览" in backtest
    assert "已完成" in results
    assert "a-share-four-factor" not in results
    assert "symbols_limit=20" in detail
    assert "参数：模板默认" in render_run(
        preview("synthetic-demo", {}, runs_root=tmp_path).run_dir
    )
