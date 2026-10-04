import hashlib
import json
from copy import deepcopy

import pytest

from quant_studio import QuantStudioError
from quant_studio.counterfactual_panel import (
    counterfactual_panel,
    load_counterfactual_view,
)
from quant_studio.counterfactual_view import source_snapshot
from quant_studio.runner import preview
from quant_studio.server import render_run, render_template_page, resolve_run_asset


def _saved(tmp_path, *, complete=True, style=True):
    run = preview("timing-counterfactual", runs_root=tmp_path).run_dir
    native = run / "strategy-output"
    output = native / "studio-view"
    output.mkdir(parents=True)
    for name in ("report.html", "periods.csv", "protocol.json", "result.json"):
        (native / name).write_text("original", encoding="utf-8")
    names = ["base", "position_neutral"]
    if style:
        names += ["style_neutral", "joint"]
    candidates = {name: {"status": "complete"} for name in names}
    if not complete:
        candidates[names[-1]] = {"status": "failed", "reason": "<input failure>"}
    effects = {
        name: {
            "net_return": 0.1 + index * 0.02,
            "effect": index * 0.02,
            "component_effects": {"AAA": index * 0.03, "base_cost": -index * 0.01},
            "reconciliation_residual": 0,
        }
        for index, name in enumerate(names)
    }
    period = {
        "id": "descriptive",
        "n_decisions": 12,
        "first_return_date": "2026-01-02",
        "last_return_date": "2026-01-20",
        "effects": effects,
        "interaction": 0 if style else None,
    }
    view = {
        "schema_version": "quant-studio.timing-counterfactual-view/v1",
        "project": "quant-timing",
        "evidence_kind": "synthetic",
        "native_exit_code": 0 if complete else 2,
        "effective_config": {"run_id": "original"},
        "receipt": {
            "status": "complete" if complete else "incomplete",
            "candidates": candidates,
            "style_applicable": style,
            "source_error": None,
            "periods": [period, dict(period, id="fold-0")] if complete else [],
        },
        "source_files": source_snapshot(native),
    }
    path = output / "view.json"
    path.write_text(json.dumps(view), encoding="utf-8")
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    result.update(
        evidence_kind="synthetic",
        status="succeeded" if complete else "failed",
        returncode=view["native_exit_code"],
        report="strategy-output/report.html",
        research_view_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return run, result, view


def test_fixed_template_has_no_parameter_search_or_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_TIMING_CONFIG", str(tmp_path / "original.yaml"))
    result = preview("timing-counterfactual", runs_root=tmp_path / "runs")
    assert "counterfactual" in result.argv and "--overrides" not in result.argv
    assert result.argv[result.argv.index("--config") + 1].endswith("original.yaml")
    with pytest.raises(QuantStudioError, match="未知参数"):
        preview("timing-counterfactual", {"cost_multiplier": 2}, runs_root=tmp_path)
    page = render_template_page("timing-counterfactual")
    assert "数据预检" in page and "全部候选禁发仓位" in page
    assert "这些字段写入上游配置" not in page


@pytest.mark.parametrize("style", [True, False])
def test_family_results_are_separate_from_publication(tmp_path, style):
    run, result, view = _saved(tmp_path, style=style)
    assert load_counterfactual_view(run, result) == view
    page = render_run(run)
    assert "研究比较完成" in page and "仓位发布：所有候选禁用" in page
    assert "描述性全区间" in page and "测试折0" in page
    assert "模型基础成本" in page and "展开分量差" in page
    assert "<iframe" not in page and "净值曲线" not in page
    assert "没有找到净值序列" not in page
    if not style:
        assert "不适用，原配置无风格模块" in page
        assert "联合交互：不适用" in page
    download = resolve_run_asset(run.parent, run.name, "strategy-output/periods.csv")
    assert download.read_text() == "original"


def test_incomplete_family_shows_failure_and_no_effects(tmp_path):
    run, _, _ = _saved(tmp_path, complete=False)
    page = render_run(run)
    assert "家族结论不可用" in page and "&lt;input failure&gt;" in page
    assert "展开分量差" not in page and "相对原策略效应" not in page
    assert "打开原生报告" in page
    assert resolve_run_asset(run.parent, run.name, "strategy-output/report.html")


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "missing",
        "extra",
        "view",
        "view_extra",
        "publication",
        "overlay",
        "curve",
        "table",
        "status",
        "exit_code",
        "report_path",
    ],
)
def test_page_and_download_reject_mutated_family(tmp_path, change):
    run, result, _ = _saved(tmp_path)
    native = run / "strategy-output"
    if change == "source":
        (native / "protocol.json").write_text("changed")
    elif change == "missing":
        (native / "report.html").unlink()
    elif change == "extra":
        (native / "new.csv").write_text("unverified")
    elif change == "view":
        with (native / "studio-view/view.json").open("a") as handle:
            handle.write(" ")
    elif change == "view_extra":
        (native / "studio-view/new.csv").write_text("unverified")
    elif change in {"publication", "overlay"}:
        target = native / "runs/failed"
        target.mkdir(parents=True)
        (
            target
            / (
                "position_scale.json"
                if change == "publication"
                else "portfolio_overlay.yaml"
            )
        ).write_text("{}")
    elif change in {"curve", "table"}:
        (run / ("nav.csv" if change == "curve" else "orders.csv")).write_text(
            "unverified"
        )
    else:
        result[
            {"status": "status", "exit_code": "returncode", "report_path": "report"}[
                change
            ]
        ] = {"status": "failed", "exit_code": 2, "report_path": "other.html"}[change]
        (run / "result.json").write_text(json.dumps(result))
    with pytest.raises(QuantStudioError):
        render_run(run)
    with pytest.raises(QuantStudioError):
        resolve_run_asset(run.parent, run.name, "strategy-output/periods.csv")


def test_missing_verification_and_unbound_download_rejected(tmp_path):
    run, result, _ = _saved(tmp_path)
    with pytest.raises(QuantStudioError, match="未通过"):
        resolve_run_asset(run.parent, run.name, "strategy-output/studio-view/view.json")
    result["research_view_sha256"] = None
    with pytest.raises(QuantStudioError, match="缺少"):
        load_counterfactual_view(run, result)
    result["status"] = "failed"
    with pytest.raises(QuantStudioError, match="缺少"):
        load_counterfactual_view(run, result)
    result["report"] = None
    assert load_counterfactual_view(run, result) is None


def test_effect_sign_and_escaping(tmp_path):
    run, _, view = _saved(tmp_path)
    modified = deepcopy(view)
    effect = modified["receipt"]["periods"][0]["effects"]["position_neutral"]
    effect["effect"] = -0.03
    effect["component_effects"] = {"<asset>": -0.03}
    page = counterfactual_panel(run, modified)
    assert "-3.0000" in page and "effect-bar negative" in page
    assert "&lt;asset&gt;" in page and "<asset>" not in page


def test_evidence_label_cannot_be_upgraded_independently(tmp_path):
    run, result, _ = _saved(tmp_path)
    result["evidence_kind"] = "retrospective"
    (run / "result.json").write_text(json.dumps(result))
    with pytest.raises(QuantStudioError, match="不一致"):
        render_run(run)
    with pytest.raises(QuantStudioError, match="不一致"):
        resolve_run_asset(run.parent, run.name, "strategy-output/periods.csv")
