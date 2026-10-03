import json
import subprocess
import sys
from decimal import Decimal, localcontext

import pytest

from quant_studio import QuantStudioError
from quant_studio.nav import NavSeries, chart_fragment, drawdown_fragment
from quant_studio.runner import Readiness, _validate_preflight, preflight, preview, run
from quant_studio.server import render_run, render_template_page
from quant_studio.standard_view import fixed_decimal
from quant_studio.templates import load_template, render_template


@pytest.mark.parametrize("name", ["futures-spread-fixture", "crypto-basis-fixture"])
def test_fixture_parameters_preserve_precise_cash_and_reject_external_input(
    name, tmp_path, monkeypatch
):
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    template = load_template(name)
    rendered = render_template(template, {"initial_cash": "250000.12345678"})
    assert rendered.values["initial_cash"] == "250000.12345678"
    with pytest.raises(QuantStudioError, match="不接受外部数据目录"):
        preview(template, snapshot=tmp_path, runs_root=tmp_path / "runs")
    for value in ("NaN", "-1", "10000.123456789", "1000 --output", 10000):
        with pytest.raises(QuantStudioError):
            render_template(template, {"initial_cash": value})
    page = render_template_page(name)
    assert "离线" in page and "初始资金" in page and "数据预检" in page
    assert 'name="snapshot"' not in page
    assert "默认数据目录未发现数据" not in page


def fixture_evidence(template):
    return {
        "schema_version": template.preflight_contract["schema_version"],
        "status": "pass",
        "read_only": True,
        "investable": False,
        "evidence_kind": "synthetic",
        "instrument_ids": ["spot", "perpetual"],
        "event_count": 17,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "other/v1"},
        {"status": "failed"},
        {"read_only": False},
        {"investable": True},
        {"evidence_kind": "historical_pit"},
        {"instrument_ids": None},
        {"instrument_ids": [None]},
        {"event_count": True},
        {"event_count": 0},
    ],
)
def test_fixture_preflight_requires_declared_contract(change):
    template = load_template("crypto-basis-fixture")
    with pytest.raises(QuantStudioError):
        _validate_preflight(template, fixture_evidence(template) | change)


def test_fixture_preflight_summary_and_same_arguments(tmp_path, monkeypatch):
    template = load_template("crypto-basis-fixture")
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "quant_studio.runner._executable", lambda *_a, **_k: sys.executable
    )
    monkeypatch.setattr(
        "quant_studio.runner.template_readiness",
        lambda *_a, **_k: Readiness(True, "ready", executable=sys.executable),
    )
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(fixture_evidence(template)), ""
        )

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    result = preflight(
        template,
        {"source": "okx", "liquidity": "taker", "initial_cash": "250000.12345678"},
        runs_root=tmp_path / "runs",
    )
    assert result.status == "checked" and len(calls) == 1
    assert "--preflight" in calls[0] and "--output" not in calls[0]
    assert "250000.12345678" in calls[0] and "taker" in calls[0]
    page = render_run(result.run_dir)
    assert "标的数：2" in page and "数据行数：17" in page
    assert 'name="initial_cash" value="250000.12345678"' in page
    assert not (result.run_dir / "strategy-output").exists()


@pytest.mark.parametrize("outcome", ["corrupt", "timeout"])
def test_projection_failure_never_becomes_success(tmp_path, monkeypatch, outcome):
    template = load_template("crypto-basis-fixture")
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "quant_studio.runner._executable", lambda *_a, **_k: sys.executable
    )
    monkeypatch.setattr(
        "quant_studio.runner.template_readiness",
        lambda *_a, **_k: Readiness(True, "ready", executable=sys.executable),
    )

    def execute(argv, **kwargs):
        if "-I" in argv:
            if outcome == "timeout":
                raise subprocess.TimeoutExpired(
                    argv, 1, output="started", stderr="timeout"
                )
            return subprocess.CompletedProcess(argv, 1, "", "mutated native evidence")
        return subprocess.CompletedProcess(argv, 0, "native success", "")

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "failed" and "展示转换" in result.message
    assert not (result.run_dir / "nav.csv").exists()
    assert (result.run_dir / "view-stderr.txt").is_file()
    assert "净值曲线" not in render_run(result.run_dir)


def test_fixed_point_projection_does_not_round_with_decimal_context():
    with localcontext() as context:
        context.prec = 5
        assert fixed_decimal(123456789012345678, 8) == Decimal("1234567890.12345678")
        assert fixed_decimal(-123456789012345678, 8) == Decimal("-1234567890.12345678")
    series = NavSeries(
        [("2026-01-02T00:00:01.001+00:00", Decimal("99999.8"))],
        initial_nav=Decimal("100000"),
    )
    chart = chart_fragment(series, currency="USDT", return_decimals=4)
    assert "USDT账户金额" in chart and "-0.0002%" in chart
    assert "01.001+00:00" in chart
    assert "0.0002%" in drawdown_fragment(series, return_decimals=4)
    tiny = NavSeries(series.rows, initial_nav=Decimal("99999.81"))
    assert "-0.000010%" in chart_fragment(tiny, return_decimals=4)
