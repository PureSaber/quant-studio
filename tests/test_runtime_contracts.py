import json
import sys
from decimal import Decimal
from functools import partial

import pytest

from quant_studio import QuantStudioError
from quant_studio.__main__ import main
from quant_studio.nav import chart_fragment, collect_outputs, parse_nav_csv
from quant_studio.runner import preview, run, template_readiness
from quant_studio.server import render_template_page
from quant_studio.templates import load_template


def test_explicit_upstream_result_bundle_accepts_timestamp_and_latest(tmp_path):
    run_dir = tmp_path / "run"
    for name in ("20260930_120000", "latest"):
        bundle = run_dir / "strategy-output" / name
        bundle.mkdir(parents=True)
        (bundle / "cumulative_returns.csv").write_text(
            ",Q1,Q5\n2026-09-01,1,1\n2026-09-02,0.9,1.1\n"
        )
        (bundle / "report.html").write_text("actual upstream report")
    template = load_template("a-share-four-factor")
    collect_outputs(
        tmp_path,
        run_dir,
        ["{output}"],
        result_files=template.metadata["result_files"],
        nav_column=template.metadata["nav_column"],
    )
    assert parse_nav_csv(run_dir / "nav.csv").period_return == Decimal("0.1")
    assert (run_dir / "report.html").read_text() == "actual upstream report"


def test_declared_result_path_cannot_escape_run(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "strategy-output").mkdir(parents=True)
    (tmp_path / "report.html").write_text("foreign")
    with pytest.raises(QuantStudioError):
        collect_outputs(
            tmp_path,
            run_dir,
            ["{output}"],
            result_files={"report.html": "../../report.html"},
        )


def test_paper_input_paths_stay_relative_to_upstream_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    result = preview("paper-sim", runs_root=tmp_path / "runs")
    import yaml

    config = yaml.safe_load((result.run_dir / "config.yaml").read_text())
    assert config["signals"]["path"] == str(
        tmp_path
        / "workspace"
        / "quant-paper-sim"
        / "tests"
        / "fixtures"
        / "signals.yaml"
    )
    assert config["regime"]["path"].endswith("regime.json")


def test_hk_snapshot_is_selectable_and_missing_snapshot_is_blocked(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    (workspace / "quant-hk-equity").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.delenv("QUANT_HK_SNAPSHOT", raising=False)
    template = load_template("hk-equity-daily")
    template.metadata["argv"][0] = sys.executable
    assert not template_readiness(template).runnable
    assert run(template, execute=True, runs_root=tmp_path / "runs").status == "blocked"
    snapshot = tmp_path / "frozen-input"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text("{}")
    result = preview(template, runs_root=tmp_path / "runs", snapshot=snapshot)
    assert result.argv[result.argv.index("--snapshot") + 1] == str(snapshot)
    assert template_readiness(template, snapshot=snapshot).runnable
    assert 'name="snapshot"' in render_template_page("hk-equity-daily")


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_cli_propagates_unsuccessful_execution(tmp_path, monkeypatch, capsys, status):
    from quant_studio.runner import RunResult

    monkeypatch.setattr(
        "quant_studio.__main__.run",
        lambda *a, **kw: RunResult(
            status, "one", tmp_path, [], returncode=1 if status == "failed" else None
        ),
    )
    assert main(["run", "paper-sim", "--execute"]) != 0
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_accepts_existing_snapshot(tmp_path, monkeypatch, capsys):
    snapshot = tmp_path / "input"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text("{}")
    monkeypatch.setattr(
        "quant_studio.__main__.run", partial(run, runs_root=tmp_path / "runs")
    )
    assert main(["preview", "hk-equity-daily", "--snapshot", str(snapshot)]) == 0
    assert str(snapshot) in json.loads(capsys.readouterr().out)["argv"]


def test_nav_sorts_dates_without_changing_returns(tmp_path):
    path = tmp_path / "nav.csv"
    path.write_text("date,nav\n2026-09-02,110\n2026-09-01,100\n")
    assert parse_nav_csv(path).period_return == Decimal("0.1")


@pytest.mark.parametrize(
    "rows",
    [
        "2026-09-01,100\n2026-09-01,110",
        "bad-date,100\n2026-09-02,110",
        "2026-09-01,100\n2026-09-02,NaN",
        "2026-09-01,0\n2026-09-02,110",
    ],
)
def test_invalid_nav_is_not_silently_repaired(tmp_path, rows):
    path = tmp_path / "nav.csv"
    path.write_text("date,nav\n" + rows + "\n")
    with pytest.raises(QuantStudioError):
        parse_nav_csv(path)


def test_multiple_strategies_require_explicit_selection(tmp_path):
    path = tmp_path / "nav.csv"
    path.write_text(
        "date,strategy,nav\n2026-09-01,A,100\n2026-09-02,A,110\n"
        "2026-09-01,B,100\n2026-09-02,B,90\n"
    )
    with pytest.raises(QuantStudioError, match="策略"):
        parse_nav_csv(path)
    assert parse_nav_csv(path, strategy="A").period_return == Decimal("0.1")
    assert parse_nav_csv(path, strategy="B").period_return == Decimal("-0.1")


def test_single_observation_is_visible_without_invented_interval_return(tmp_path):
    path = tmp_path / "nav.csv"
    path.write_text("date,nav\n2026-09-01,99985.60\n")
    series = parse_nav_csv(path)
    assert series is not None and series.ending == Decimal("99985.60")
    assert series.period_return is None and series.max_drawdown is None
    assert "单次观测" in chart_fragment(series)
