from quant_studio.form_controls import special_control
from quant_studio.research_web import _leaves, _read_fields


def test_allocation_editor_keeps_native_protocol_and_roundtrips():
    import json

    value = {
        "protocol_version": 2,
        "mode": "cost_aware",
        "risk_objective": "absolute",
        "forecast_horizon_sessions": 5,
        "expected_return_model": "ic_vol",
    }
    base = {"allocation": value}
    assert list(_leaves(base)) == [("/allocation", "allocation", value)]
    html = special_control("config/allocation", "allocation", value)
    assert "allocation-editor" in html and "max_sharpe" in html and "ic_vol" in html
    assert (
        _read_fields(base, {"config/allocation": [json.dumps(value)]}, "config") == base
    )


def test_barra_editor_hides_dense_only_targets_and_groups_exposure_limits():
    value = {
        "protocol_version": 2,
        "mode": "cost_aware",
        "risk_objective": "active",
        "forecast_horizon_sessions": 5,
        "expected_return_model": "ic_vol",
    }
    html = special_control("config/allocation", "allocation", value, factor_risk=True)
    assert "hrp" not in html and "risk_parity" not in html
    limits = {
        "benchmark_weights": {"A": 0.5, "B": 0.5},
        "factor_bounds": {"size": [-1, 1]},
    }
    assert len(list(_leaves(limits))) == 2
