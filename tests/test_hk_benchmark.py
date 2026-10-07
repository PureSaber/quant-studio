import json
from decimal import Decimal

import pytest

from quant_studio import QuantStudioError
from quant_studio.nav import (
    NavSeries,
    collect_outputs,
    comparison_fragment,
    parse_nav_csv,
)
from quant_studio.runner import preview
from quant_studio.server import render_run
from quant_studio.templates import load_template


def _collected(tmp_path):
    result = preview("hk-equity-daily", {"initial_cash": "100000"}, runs_root=tmp_path)
    output = result.run_dir / "strategy-output"
    mapping = load_template("hk-equity-daily").metadata["result_files"]
    for target, relative in mapping.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if target in {"nav.csv", "benchmark_nav.csv"}:
            name, first, last = (
                ("low_volatility_20d", "50000", "101000")
                if target == "nav.csv"
                else ("equal_weight", "90000", "104000")
            )
            path.write_text(
                "date,strategy,nav\n"
                f"2026-01-02,{name},{first}\n2026-01-05,{name},{last}\n"
            )
        else:
            path.write_text("native report" if target == "report.html" else "date\n")
    before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    collect_outputs(
        tmp_path,
        result.run_dir,
        ["{output}"],
        result_files=mapping,
        initial_nav="100000",
    )
    assert before == {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    result.status = "succeeded"
    (result.run_dir / "result.json").write_text(json.dumps(result.as_json()))
    return result.run_dir


def test_native_benchmark_is_collected_and_compared_from_same_opening(tmp_path):
    directory = _collected(tmp_path)
    baseline = parse_nav_csv(directory / "benchmark_nav.csv")
    assert baseline.initial_nav == Decimal("100000")
    assert baseline.period_return == Decimal("0.04")
    assert baseline.max_drawdown == Decimal("0.1")
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    page = render_run(directory)
    assert "策略与基准累计收益" in page and "同池等权基准" in page
    assert "+1.00%" in page and "+4.00%" in page and "-3.00个百分点" in page
    assert "50.00%" in page and "100000HKD" in page
    assert "未提供基准净值" not in page
    assert "首日损益计入" in page and "当前研究观察名单" in page
    assert f"/runs/{directory.name}/files/benchmark_nav.csv" in page
    assert before == {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "contents,message",
    [
        ("date,nav\n2026-01-02,90000\n2026-01-06,104000\n", "观测日期"),
        (
            "date,nav,initial_nav\n2026-01-02,90000,200000\n2026-01-05,104000,200000\n",
            "期初净值",
        ),
        ("date,nav\n", "基准文件"),
    ],
)
def test_render_rejects_mismatched_or_empty_saved_benchmark(
    tmp_path, contents, message
):
    directory = _collected(tmp_path)
    (directory / "benchmark_nav.csv").write_text(contents)
    with pytest.raises(QuantStudioError, match=message):
        render_run(directory)


def test_new_hk_run_requires_native_benchmark(tmp_path):
    directory = _collected(tmp_path)
    mapping = load_template("hk-equity-daily").metadata["result_files"]
    (directory / "strategy-output" / mapping["benchmark_nav.csv"]).unlink()
    with pytest.raises(QuantStudioError, match="本次运行结果缺失"):
        collect_outputs(
            tmp_path,
            directory,
            ["{output}"],
            result_files=mapping,
            initial_nav="100000",
        )


def test_comparison_keeps_first_close_loss_and_escapes_labels():
    strategy = NavSeries([("2026-01-02", Decimal(50))], initial_nav=Decimal(100))
    benchmark = NavSeries([("2026-01-02", Decimal(90))], initial_nav=Decimal(100))
    page = comparison_fragment(
        strategy, benchmark, benchmark_label="<script>", currency="HKD"
    )
    assert "-50.00%" in page and "-10.00%" in page and "-40.00个百分点" in page
    assert "&lt;script&gt;" in page and "<script>" not in page
    assert "期初" in page


def test_comparison_requires_known_equal_initial_capital():
    unknown = NavSeries([("2026-01-02", Decimal(50))])
    with pytest.raises(QuantStudioError, match="明确的期初本金"):
        comparison_fragment(unknown, unknown, benchmark_label="基准", currency="HKD")
    known = NavSeries(unknown.rows, initial_nav=Decimal(100))
    with pytest.raises(QuantStudioError, match="期初净值必须一致"):
        comparison_fragment(unknown, known, benchmark_label="基准", currency="HKD")
