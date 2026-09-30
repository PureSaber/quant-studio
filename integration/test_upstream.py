"""Run against pinned real upstream packages; no broker or network market-data calls."""

import importlib.util
import os
from pathlib import Path

from quant_studio.nav import collect_outputs, parse_nav_csv
from quant_studio.runner import run
from quant_studio.server import render_run
from quant_studio.templates import Template, load_template


def diagnostics(result):
    stderr = result.run_dir / "stderr.txt"
    return (
        result.as_json(),
        stderr.read_text(encoding="utf-8") if stderr.exists() else "",
    )


def test_paper_cli(tmp_path, monkeypatch):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    result = run("paper-sim", execute=True, runs_root=tmp_path, timeout=90)
    assert result.status == "succeeded", diagnostics(result)
    assert result.returncode == 0
    nav = parse_nav_csv(result.run_dir / "nav.csv")
    assert len(nav.rows) == 1 and nav.period_return is None
    assert (result.run_dir / "strategy-output" / "execution_log.json").is_file()
    assert "单次观测" in render_run(result.run_dir)


def test_hk_cli(tmp_path, monkeypatch):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    spec = importlib.util.spec_from_file_location(
        "hk_fixture", repo / "tests/test_research.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    case = fixture.case.__wrapped__()
    snapshot = fixture.snapshot(tmp_path, case)
    original = load_template("hk-equity-daily")
    template = Template(original.metadata, case[0], original.directory)
    result = run(
        template,
        execute=True,
        snapshot=snapshot,
        runs_root=tmp_path / "runs",
        timeout=90,
    )
    assert result.status == "succeeded", diagnostics(result)
    assert result.returncode == 0
    assert len(parse_nav_csv(result.run_dir / "nav.csv").rows) > 20
    assert (result.run_dir / "positions.csv").is_file()
    assert "<iframe" in render_run(result.run_dir)


def test_ashare_real_output_contract(tmp_path):
    import pandas as pd
    from a_share_multifactor.backtest import write_outputs
    from a_share_multifactor.config import AppConfig
    from a_share_multifactor.quantile_backtest import BacktestResult

    output = tmp_path / "strategy-output"
    dates = pd.to_datetime(["2026-09-01", "2026-09-02"])
    results = BacktestResult(
        quantile_returns=pd.DataFrame({"Q5": [0.0, 0.01]}, index=dates),
        cumulative_returns=pd.DataFrame({"Q5": [1.0, 1.01]}, index=dates),
        long_short=pd.Series([0.0, 0.01], index=dates),
        stats=pd.DataFrame({"portfolio": ["Q5"], "mean_return": [0.005]}),
    )
    write_outputs(
        results,
        pd.DataFrame({"factor": ["fixture"], "mean_ic": [0.05]}),
        AppConfig(outputs_dir=str(output)),
    )
    assert len([p for p in output.iterdir() if p.is_dir()]) == 2
    template = load_template("a-share-four-factor")
    collect_outputs(
        tmp_path,
        tmp_path,
        ["{output}"],
        result_files=template.metadata["result_files"],
        nav_column=template.metadata["nav_column"],
    )
    assert parse_nav_csv(tmp_path / "nav.csv").label == "Q5"
    assert (tmp_path / "report.html").is_file()
