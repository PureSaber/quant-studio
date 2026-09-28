import copy

import pytest

from quant_studio import QuantStudioError
from quant_studio.templates import (
    apply_factor_selection,
    load_template,
    render_template,
)


def test_a_share_render_changes_only_declared_config_fields():
    template = load_template("a-share-four-factor")
    original = copy.deepcopy(template.base_config)

    rendered = render_template(
        template,
        {
            "rebalance_freq": "weekly",
            "initial_capital": 250000,
            "symbols_limit": 25,
        },
    )

    assert rendered.config["rebalance_freq"] == "weekly"
    assert rendered.config["costs"]["initial_capital"] == 250000
    assert rendered.config["factors"] == [
        "pe_ratio",
        "pb_ratio",
        "market_cap",
        "forecast_score",
        "momentum_20d",
        "volatility_20d",
        "northbound_chg_5d",
        "industry_rs_20d",
    ]
    assert template.base_config == original
    assert rendered.values["symbols_limit"] == 25


def test_factor_selection_keeps_only_known_factors():
    template = load_template("a-share-four-factor")
    rendered = render_template(template, {})

    apply_factor_selection(rendered.config, template, ["momentum_20d", "pe_ratio"])

    assert rendered.config["factors"] == ["momentum_20d", "pe_ratio"]
    assert rendered.config["factor_directions"]["momentum_20d"] == 1
    assert rendered.config["factor_directions"]["pe_ratio"] == -1


def test_unknown_factor_is_rejected():
    template = load_template("a-share-four-factor")
    rendered = render_template(template, {})

    with pytest.raises(QuantStudioError, match="未知因子"):
        apply_factor_selection(rendered.config, template, ["alpha99"])


@pytest.mark.parametrize(
    ("template_id", "knobs", "name"),
    [
        ("a-share-four-factor", {"mystery": 1}, "mystery"),
        ("a-share-four-factor", {"symbols_limit": 301}, "symbols_limit"),
        ("a-share-four-factor", {"initial_capital": "10000"}, "initial_capital"),
        ("hk-equity-daily", {"rebalance_sessions": 3}, "rebalance_sessions"),
        ("hk-equity-daily", {"initial_cash": "1e6"}, "initial_cash"),
        ("hk-equity-daily", {"initial_cash": "١٠٠٠"}, "initial_cash"),
    ],
)
def test_invalid_knobs_raise_clear_errors(template_id, knobs, name):
    with pytest.raises(QuantStudioError, match=name):
        render_template(load_template(template_id), knobs)


def test_hk_render_preserves_unmodified_baseline_values():
    template = load_template("hk-equity-daily")

    rendered = render_template(
        template, {"initial_cash": "2000000", "rebalance_sessions": 1}
    )

    assert rendered.config["initial_cash"] == "2000000"
    assert rendered.config["rebalance_sessions"] == 1
    assert rendered.config["instruments"] == template.base_config["instruments"]
    assert rendered.config["fees"] == template.base_config["fees"]
    assert rendered.config["data_start"] == "2025-07-01"
    assert rendered.config["top_n"] == 3
    assert rendered.config["instruments"][0]["lot_size"] == 400
