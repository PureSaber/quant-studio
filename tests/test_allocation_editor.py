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
