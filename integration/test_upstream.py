"""Run against pinned real upstream packages; no broker or network market-data calls."""

import csv
import hashlib
import importlib.util
import json
import os
import re
import threading
from datetime import UTC
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import urlopen

import pytest

from quant_studio.nav import collect_outputs, parse_nav_csv
from quant_studio.runner import preflight, run
from quant_studio.server import _Handler, render_run
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
    source_files = [
        repo / "tests/fixtures/signals.yaml",
        repo / "tests/fixtures/regime.json",
    ]
    before = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in source_files
    }
    checked = preflight("paper-sim", runs_root=tmp_path / "checks")
    assert checked.status == "checked", diagnostics(checked)
    assert not (checked.run_dir / "strategy-output").exists()
    assert before == {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in source_files
    }
    result = run("paper-sim", execute=True, runs_root=tmp_path, timeout=90)
    assert result.status == "succeeded", diagnostics(result)
    assert result.returncode == 0
    assert result.verification_status == "pass"
    assert "账本重放校验通过" in render_run(result.run_dir)
    nav = parse_nav_csv(result.run_dir / "nav.csv")
    assert len(nav.rows) == 1 and nav.period_return is None
    assert (result.run_dir / "strategy-output" / "execution_log.json").is_file()
    assert "单次观测" in render_run(result.run_dir)


@pytest.mark.parametrize("first_close_ratio", [0.5, 1.1, 1.0])
def test_hk_cli(tmp_path, monkeypatch, first_close_ratio):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    spec = importlib.util.spec_from_file_location(
        "hk_fixture", repo / "tests/test_research.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    case = fixture.case.__wrapped__()
    config, bars, _ = case
    first = bars.date.eq(config["test_start"])
    bars.loc[first, "close"] = bars.loc[first, "open"] * first_close_ratio
    bars.loc[first, "low"] = bars.loc[first, ["open", "close"]].min(axis=1) * 0.99
    bars.loc[first, "high"] = bars.loc[first, ["open", "close"]].max(axis=1) * 1.01
    snapshot = fixture.snapshot(tmp_path, case)
    original = load_template("hk-equity-daily")
    template = Template(original.metadata, case[0], original.directory)
    before = {
        str(path): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    checked = preflight(template, snapshot=snapshot, runs_root=tmp_path / "checks")
    assert checked.status == "checked", diagnostics(checked)
    assert not (checked.run_dir / "strategy-output").exists()
    assert (
        json.loads((checked.run_dir / "preflight.json").read_text())["investable"]
        is False
    )
    assert before == {
        str(path): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in snapshot.rglob("*")
        if path.is_file()
    }
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
    nav = parse_nav_csv(result.run_dir / "nav.csv")
    native_summary = json.loads(
        (result.run_dir / "strategy-output/summary.json").read_text(encoding="utf-8")
    )
    summary = native_summary["holdout"]
    assert float(nav.initial_nav) == float(config["initial_cash"])
    assert float(nav.period_return) == pytest.approx(summary["total_return"], abs=1e-12)
    assert float(nav.max_drawdown) == pytest.approx(-summary["max_drawdown"], abs=1e-12)
    baseline = parse_nav_csv(result.run_dir / "benchmark_nav.csv")
    assert baseline.initial_nav == nav.initial_nav
    assert [date for date, _ in baseline.rows] == [date for date, _ in nav.rows]
    with (result.run_dir / "strategy-output/benchmark/standard/returns.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        assert baseline.rows == [
            (row["date"], Decimal(row["nav"])) for row in csv.DictReader(stream)
        ]
    for observed, key in (
        (baseline.period_return, "total_return"),
        (-baseline.max_drawdown, "max_drawdown"),
        (baseline.ending, "ending_nav_hkd"),
    ):
        assert float(observed) == pytest.approx(
            native_summary["benchmark"][key], abs=1e-12
        )
    page = render_run(result.run_dir)
    assert "策略与基准累计收益" in page and "同池等权基准" in page
    assert "未提供基准净值" not in page
    assert before == {
        str(path): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    assert result.report == "strategy-output/report.html"
    _verify_report_http(result)


def _verify_report_http(result):
    class Handler(_Handler):
        runs_root = result.run_dir.parent

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/runs/{result.run_id}/files/"
    try:
        report_url = base + result.report
        with urlopen(report_url, timeout=5) as response:
            report = response.read().decode("utf-8")
        links = re.findall(r'href="([^"]+)"', report)
        assert len(links) == 7
        for href in links:
            with urlopen(urljoin(report_url, href), timeout=5) as response:
                expected_type = (
                    "application/json" if href.endswith(".json") else "text/csv"
                )
                assert response.headers.get_content_type() == expected_type
                original = result.run_dir / Path(result.report).parent / href
                assert response.read() == original.read_bytes()
        for forbidden in (
            "result.json",
            "../result.json",
            "strategy-output/../../result.json",
        ):
            with pytest.raises(HTTPError) as error:
                urlopen(base + forbidden, timeout=5)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
    report = collect_outputs(
        tmp_path,
        tmp_path,
        ["{output}"],
        result_files=template.metadata["result_files"],
        nav_column=template.metadata["nav_column"],
    )
    assert parse_nav_csv(tmp_path / "nav.csv").label == "Q5"
    assert report == "strategy-output/latest/report.html"
    assert (tmp_path / report).is_file()


@pytest.mark.parametrize(
    "bad_source", ["missing_prices", "other_benchmark", "unidentified_benchmark"]
)
def test_ashare_native_preflight(tmp_path, monkeypatch, bad_source):
    import pandas as pd

    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    spec = importlib.util.spec_from_file_location(
        "ashare_fixture", repo / "tests/test_preflight.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    _, config = fixture.cached_case(tmp_path)
    from dataclasses import asdict

    original = load_template("a-share-four-factor")
    metadata = dict(original.metadata)
    metadata["preflight_argv"] = [
        *metadata["preflight_argv"],
        "--data-dir",
        str(tmp_path),
    ]
    template = Template(metadata, asdict(config), original.directory)
    before = fixture.snapshot(tmp_path)
    checked = preflight(template, runs_root=tmp_path / "checks")
    assert checked.status == "checked", diagnostics(checked)
    assert not (checked.run_dir / "strategy-output").exists()
    after = fixture.snapshot(tmp_path)
    assert all(after[path] == state for path, state in before.items())
    assert not (tmp_path / "snapshots").exists()
    if bad_source == "missing_prices":
        (tmp_path / "prices.parquet").unlink()
    else:
        benchmark_path = tmp_path / "benchmark.parquet"
        benchmark = pd.read_parquet(benchmark_path)
        if bad_source == "other_benchmark":
            benchmark["benchmark_symbol"] = "OTHER_INDEX"
        else:
            benchmark = benchmark.drop(columns=["benchmark_symbol"])
        benchmark.to_parquet(benchmark_path, index=False)
    failed_source_before = {
        name: state
        for name, state in fixture.snapshot(tmp_path).items()
        if name in before
    }
    rejected = preflight(template, runs_root=tmp_path / "checks")
    assert rejected.status == "check_failed", diagnostics(rejected)
    assert not (rejected.run_dir / "strategy-output").exists()
    failed_source_after = fixture.snapshot(tmp_path)
    assert all(
        failed_source_after[name] == state
        for name, state in failed_source_before.items()
    )
    if bad_source == "missing_prices":
        assert not (tmp_path / "prices.parquet").exists()
    else:
        assert "H00300" in (rejected.run_dir / "stderr.txt").read_text(encoding="utf-8")


def test_account_read_only_cli(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from quant_lab.trials import TrialRegistry
    from quant_pipeline.research_paper import publish_account

    from quant_studio.accounts import AccountSource, inspect_source

    root = tmp_path / "frozen account"
    now = datetime.now(UTC)
    registry = TrialRegistry(root / "account.db")
    registry.register(
        "frozen",
        {
            "hypothesis": "synthetic integration only",
            "parameters": [{"name": "base"}],
            "code_identity": {"fixture": "frozen"},
            "selection_rule": "fixed",
            "holdout_start": (now.date() + timedelta(days=2)).isoformat(),
            "holdout_end": (now.date() + timedelta(days=20)).isoformat(),
            "source_scope": "synthetic-software-validation",
        },
        now=now,
    )
    publish_account(root, "frozen", registry.definition("frozen"))
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
    result = inspect_source(AccountSource("one", "已冻结账户", root))
    assert result["state"] == "pending" and result["latest"] is None
    assert result["read_only"] is True
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()
    }
    # Inspect never reconstructs an absent authoritative database.
    (root / "account.db").unlink()
    from quant_studio import QuantStudioError

    with pytest.raises(QuantStudioError, match="核验失败"):
        inspect_source(AccountSource("one", "已冻结账户", root))
    assert not (root / "account.db").exists()
