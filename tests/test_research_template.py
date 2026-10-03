import json
import subprocess
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.runner import (
    Readiness,
    _validate_preflight,
    preflight,
    preview,
    template_readiness,
)
from quant_studio.server import render_run, render_template_page
from quant_studio.templates import load_template


def evidence():
    return {
        "schema_version": "quant-stat-arb.preflight/v1",
        "software_preflight": "pass",
        "read_only": True,
        "investable": False,
        "evidence_kind": "unspecified",
        "rows": 320,
        "symbols": 4,
        "folds": 3,
    }


def test_config_file_readiness_and_preview_preserve_original_path(
    tmp_path, monkeypatch
):
    template = load_template("stat-arb-research")
    (tmp_path / template.workspace_repo).mkdir()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("QUANT_STAT_ARB_CONFIG", raising=False)
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    template.metadata["argv"] = [sys.executable, "--version"]
    assert template_readiness(template).needs_input
    assert not template_readiness(template, snapshot=tmp_path).runnable
    source = tmp_path / "source configuration.yaml"
    source.write_text("run_id: original\n", encoding="utf-8")
    monkeypatch.setenv("QUANT_STAT_ARB_CONFIG", str(source))
    assert template_readiness(template).runnable
    original = load_template("stat-arb-research")
    result = preview(original, runs_root=tmp_path / "runs")
    assert result.argv[result.argv.index("--config") + 1] == str(source)
    override = result.run_dir / "config.json"
    assert result.argv[result.argv.index("--overrides") + 1] == str(override)
    assert json.loads(override.read_text()) == {
        "gross_budget_scale": 1,
        "cost_multiplier": 1,
    }
    assert source.read_text() == "run_id: original\n"


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "other/v1"},
        {"software_preflight": "fail"},
        {"read_only": False},
        {"investable": True},
        {"evidence_kind": "historical_pit"},
        {"rows": 0},
        {"symbols": True},
        {"folds": "3"},
        {"folds": None},
    ],
)
def test_research_contract_rejects_invalid_counts_or_evidence(change):
    with pytest.raises(QuantStudioError):
        _validate_preflight(load_template("stat-arb-research"), evidence() | change)


def test_readonly_research_preflight_form_keeps_parameters_and_source(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    monkeypatch.setattr(
        "quant_studio.runner.template_readiness",
        lambda *_a, **_k: Readiness(True, "ready", executable=sys.executable),
    )
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(evidence()), "")

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    result = preflight(
        "stat-arb-research",
        {"gross_budget_scale": 0.5, "cost_multiplier": 2},
        snapshot=tmp_path / "original.yaml",
        runs_root=tmp_path / "runs",
    )
    assert result.status == "checked" and len(calls) == 1
    assert "--out" not in calls[0] and "preflight" in calls[0]
    assert not (result.run_dir / "strategy-output").exists()
    page = render_run(result.run_dir)
    assert "标的数：4" in page and "数据行数：320" in page
    assert "来源性质未声明" in page
    assert 'name="gross_budget_scale" value="0.5"' in page
    assert 'name="cost_multiplier" value="2"' in page
    assert 'name="snapshot"' in page and "original.yaml" in page
    assert "使用相同参数运行" in page
    assert "统计套利研究配置文件" in render_template_page("stat-arb-research")


def test_research_result_uses_weight_and_return_units(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    result = preview("stat-arb-research", runs_root=tmp_path / "runs")
    path = result.run_dir
    (path / "result.json").write_text(
        json.dumps(
            result.as_json() | {"status": "succeeded", "evidence_kind": "retrospective"}
        )
    )
    (path / "nav.csv").write_text("date,nav\n2026-01-02,0.99\n2026-01-05,1.01\n")
    (path / "positions.csv").write_text("symbol,quantity,market_value\nAAA,0.2,0.2\n")
    (path / "orders.csv").write_text("symbol,quantity,status\nAAA,0.2,target\n")
    (path / "costs.csv").write_text("commission,total_cost\n0.001,0.002\n")
    page = render_run(path)
    assert "回顾性历史数据" in page and "未提供基准净值" in page
    assert "研究目标变动" in page and "研究持仓权重" in page
    assert 'title="quantity">研究权重' in page
    assert 'title="commission">佣金比例' in page
    assert "费用合计比例" in page and "没有真实股数或货币本金" in page
    assert "账户金额" not in page and "<h2>委托</h2>" not in page


@pytest.mark.parametrize(
    "template_id,environment",
    [
        ("fund-fof", "QUANT_FUND_DATASET"),
        ("us-equity-research", "QUANT_US_BUNDLE"),
        ("stat-arb-research", "QUANT_STAT_ARB_CONFIG"),
    ],
)
def test_initial_command_preview_uses_declared_source(
    template_id, environment, monkeypatch
):
    monkeypatch.setenv("QUANT_HK_SNAPSHOT", "unrelated-hk-source")
    monkeypatch.setenv(environment, "chosen-research-source")
    page = render_template_page(template_id)
    assert "unrelated-hk-source" not in page
    assert "chosen-research-source" in page.split('<pre id="compiled">')[1]
    if template_id == "stat-arb-research":
        assert "默认数据目录未发现数据" not in page
