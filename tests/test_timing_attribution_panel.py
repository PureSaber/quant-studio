from quant_studio.timing_attribution_panel import attribution_panel


def _view():
    period = {
        "id": "descriptive",
        "n_decisions": 2,
        "first_return_date": "2026-01-02",
        "last_return_date": "2026-01-05",
        "net_return": 0.02,
        "matched_net_return": 0.01,
        "benchmark_return": 0.03,
        "excess_return": -0.01,
        "matched_excess_return": 0.01,
        "strategy_residual": 0,
        "matched_residual": 0,
        "components": [
            {
                "component": "<asset>",
                "kind": "asset",
                "strategy": 0.03,
                "matched": 0.02,
                "difference": 0.01,
            },
            {
                "component": "base_cost",
                "kind": "modeled_cost",
                "strategy": -0.01,
                "matched": -0.01,
                "difference": 0,
            },
        ],
    }
    return {
        "native_exit_code": 0,
        "return_attribution": {
            "periods": [
                period,
                {
                    **period,
                    "id": "fold-1",
                    "test_start": "2026-01-01",
                    "test_end": "2026-01-02",
                },
            ]
        },
    }


def test_contributions_have_point_units_signs_fold_boundaries_and_escaped_labels():
    page = attribution_panel("run-1", _view())
    assert "+3.0000" in page and "-1.0000" in page
    assert "&lt;asset&gt;" in page and "<asset>" not in page
    assert "贡献差<br>百分点" in page
    assert "策略减市场基准-1.0000个百分点" in page
    assert "contribution-bar negative" in page
    assert "<details" in page and "测试折1" in page
    assert "未重新建仓，各折不拼接" in page
    assert "不单独证明因果效应" in page
    assert 'attribution_daily.csv" download' in page


def test_blocked_and_legacy_runs_do_not_receive_invented_attribution():
    view = _view()
    view["native_exit_code"] = 2
    page = attribution_panel("blocked", view)
    assert "不展示收益归因" in page and "<table>" not in page
    assert "此历史运行未导出收益归因" in attribution_panel(
        "old", {"native_exit_code": 0}
    )


def test_empty_fold_is_unavailable_and_zero_contributions_render_without_division():
    view = _view()
    for component in view["return_attribution"]["periods"][0]["components"]:
        component.update(strategy=0, matched=0, difference=0)
    view["return_attribution"]["periods"][1]["n_decisions"] = 0
    page = attribution_panel("zero", view)
    assert "width:0.00%" in page
    assert "没有可计分收益，贡献不可用" in page
