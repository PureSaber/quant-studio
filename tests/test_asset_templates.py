import json
import sys
from decimal import Decimal

import pytest

from quant_studio import QuantStudioError
from quant_studio.nav import collect_outputs, parse_nav_csv
from quant_studio.runner import _evidence_kind, preview, template_readiness
from quant_studio.server import compiled_from_form, render_template_page
from quant_studio.templates import load_template, render_template


@pytest.mark.parametrize(
    "name,environment,marker,label",
    [
        ("fund-fof", "QUANT_FUND_DATASET", "dataset.json", "基金数据集"),
        ("us-equity-research", "QUANT_US_BUNDLE", "manifest.json", "美股数据包"),
    ],
)
def test_each_asset_uses_own_input_contract_and_never_hk_default(
    name, environment, marker, label, tmp_path, monkeypatch
):
    template = load_template(name)
    (tmp_path / template.workspace_repo).mkdir()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv(environment, raising=False)
    monkeypatch.setenv("QUANT_HK_SNAPSHOT", str(tmp_path / "unrelated-hk"))
    template.metadata["argv"] = [sys.executable, "--version"]
    ready = template_readiness(template)
    assert not ready.runnable and ready.needs_input and label in ready.message
    source = tmp_path / "selected source"
    source.mkdir()
    for filename in template.metadata["input_source"]["required_files"]:
        (source / filename).write_text("{}")
    monkeypatch.setenv(environment, str(source))
    assert template_readiness(template).runnable
    original = load_template(name)
    result = preview(original, runs_root=tmp_path / "runs")
    assert str(source) in result.argv
    assert (
        str(source)
        == json.loads((result.run_dir / "request.json").read_text())["snapshot"]
    )
    (source / marker).unlink()
    assert not template_readiness(template).runnable


@pytest.mark.parametrize(
    "name,settings",
    [
        ("fund-fof", {"start": "2026-02-30"}),
        ("fund-fof", {"end": "20260101"}),
        ("us-equity-research", {"benchmark": "US:SPY\n--output"}),
        ("us-equity-research", {"benchmark": ""}),
    ],
)
def test_dates_and_explicit_benchmark_are_validated(name, settings):
    with pytest.raises(QuantStudioError):
        render_template(load_template(name), settings)


def test_form_preserves_currency_dates_and_source_values():
    html = render_template_page("fund-fof")
    assert "人民币" in html and 'type="date"' in html and "基金数据集目录" in html
    html = render_template_page("us-equity-research")
    assert "美元" in html and "包内基准证券ID" in html
    compiled = compiled_from_form(
        "us-equity-research",
        {
            "initial_cash": ["250000"],
            "benchmark": ["US:DEMO:SPY"],
            "snapshot": ["selected-input"],
        },
    )
    assert (
        "250000" in compiled
        and "US:DEMO:SPY" in compiled
        and "selected-input" in compiled
    )


def test_fund_unit_nav_and_us_cash_nav_have_distinct_opening_values(tmp_path):
    for name, header, values, opening in [
        ("fund-fof", "date,nav", "2026-01-02,0.98\n2026-01-05,1.02", 1),
        (
            "us-equity-research",
            "session,nav",
            "2026-01-02,98000\n2026-01-05,102000",
            100000,
        ),
    ]:
        run = tmp_path / name
        output = run / "strategy-output"
        output.mkdir(parents=True)
        (output / "nav.csv").write_text(header + "\n" + values + "\n")
        collect_outputs(
            tmp_path,
            run,
            ["{output}"],
            result_files={"nav.csv": "nav.csv"},
            initial_nav=opening,
        )
        series = parse_nav_csv(run / "nav.csv")
        assert series.initial_nav == Decimal(opening)
        assert series.period_return == Decimal("0.02")
        assert series.max_drawdown == Decimal("0.02")


@pytest.mark.parametrize(
    "name,field",
    [
        ("fund-fof", "classification"),
        ("us-equity-research", "evidence_kind"),
    ],
)
def test_missing_or_unknown_data_class_cannot_be_presented_as_success(name, field):
    template = load_template(name)
    assert _evidence_kind(template, {field: "synthetic"}) == "synthetic"
    for record in ({}, {field: "certified-live"}, [], None):
        with pytest.raises(QuantStudioError, match="数据性质"):
            _evidence_kind(template, record)


def test_benchmark_requires_identical_dates_and_same_opening_cash(tmp_path):
    output = tmp_path / "strategy-output"
    output.mkdir()
    (output / "nav.csv").write_text(
        "session,nav\n2026-01-02,98000\n2026-01-05,102000\n"
    )
    path = output / "benchmark_nav.csv"
    path.write_text("session,nav\n2026-01-02,99000\n2026-01-05,104000\n")
    mapping = {"nav.csv": "nav.csv", "benchmark_nav.csv": "benchmark_nav.csv"}
    collect_outputs(
        tmp_path, tmp_path, ["{output}"], result_files=mapping, initial_nav=100000
    )
    baseline = parse_nav_csv(tmp_path / "benchmark_nav.csv")
    assert baseline.period_return == Decimal("0.04")
    assert baseline.max_drawdown == Decimal("0.01")
    path.write_text("session,nav\n2026-01-02,99000\n2026-01-06,104000\n")
    with pytest.raises(QuantStudioError, match="观测日期"):
        collect_outputs(
            tmp_path, tmp_path, ["{output}"], result_files=mapping, initial_nav=100000
        )
