import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.__main__ import main
from quant_studio.runner import preview, run
from quant_studio.server import (
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
    assert "<svg" not in page
    assert "<iframe" not in page


def test_synthetic_result_embeds_report_and_shows_disclaimer(tmp_path):
    result = run("synthetic-demo", {}, execute=True, runs_root=tmp_path)

    page = render_run(result.run_dir)

    assert "合成样例，不是市场收益" in page
    assert f"/runs/{result.run_id}/files/report.html" in page


def test_legacy_hk_results_use_frozen_opening_and_original_report_without_rewriting(
    tmp_path,
):
    result = preview("hk-equity-daily", runs_root=tmp_path)
    directory = result.run_dir
    output = directory / "strategy-output"
    output.mkdir()
    (output / "report.html").write_text('<a href="summary.json">evidence</a>')
    (directory / "report.html").write_text("old copied report")
    (directory / "nav.csv").write_text(
        "date,nav\n2026-09-28,500000\n2026-09-29,1010000\n"
    )
    payload = result.as_json()
    payload.update(status="succeeded", report="report.html")
    (directory / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    page = render_run(directory)
    assert "+1.00%" in page and "50.00%" in page
    assert f"/runs/{result.run_id}/files/strategy-output/report.html" in page
    assert before == {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_only_run_html_csv_and_upstream_json_evidence_can_be_served(tmp_path):
    run_dir = tmp_path / "safe-run"
    run_dir.mkdir()
    (run_dir / "report.html").write_text("ok", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps({}), encoding="utf-8")
    evidence = run_dir / "strategy-output" / "holdout" / "ledger.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}", encoding="utf-8")

    assert resolve_run_asset(tmp_path, "safe-run", "report.html").is_file()
    assert (
        resolve_run_asset(tmp_path, "safe-run", "strategy-output/holdout/ledger.json")
        == evidence
    )
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
    from tests.test_runner import test_current_run_outputs_are_isolated_and_collected

    test_current_run_outputs_are_isolated_and_collected(tmp_path, monkeypatch)
    run_dir = next((tmp_path / "runs").iterdir())
    page = render_run(run_dir)

    assert "<svg" in page
    assert "最大回撤" in page
    assert "期末净值" in page
    assert "+0.09%" in page
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
    assert "a_share_multifactor.fetch_data" in page


def test_backtest_and_results_list_runs(tmp_path):
    preview("synthetic-demo", {}, runs_root=tmp_path)

    assert "synthetic-demo" in render_backtest(tmp_path)
    assert "synthetic-demo" in render_results(tmp_path)
