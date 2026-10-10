import json
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.derivative_datasets import collection_request, command
from quant_studio.research_web import editor_body
from quant_studio.runtime import template_python
from quant_studio.settings import use_settings
from quant_studio.setup import empty_profile, repositories, validate_shape
from quant_studio.templates import load_template, render_template


def test_templates_and_separate_certified_runtime():
    profile = empty_profile()
    profile["python_by_repo"] = {"quant-futures-global": sys.executable}
    validate_shape(profile)
    assert "quant-futures-global" in repositories()
    with use_settings(profile):
        global_template = load_template("global-futures-research")
        assert template_python(global_template) == sys.executable
        assert template_python(load_template("futures-spread-fixture")) is None
    options = load_template("options-research")
    assert (
        render_template(options, {"legs": "EXCHANGE:CALL=1,EXCHANGE:PUT=-1"})
        .config["legs"]
        .endswith("PUT=-1")
    )
    assert global_template.workspace_repo == "quant-futures-spread"
    page = editor_body("options-research", "token")
    assert "研究范围" in page and 'name="cfg:/mode"' in page
    assert "组合：合约 ID=手数" in page


def test_derivative_collection_freezes_rules_and_caps_spend(tmp_path):
    rules = tmp_path / "contracts.json"
    rules.write_text(json.dumps([{"kind": "option", "symbol": "C1"}]))
    req = collection_request(
        "real",
        "options-research",
        "databento",
        str(rules),
        "2025-01-01",
        "2025-01-03",
        "authorized research",
        "GLBX.MDP3",
        ".25",
    )
    rules.write_text("[]")
    assert req["contracts"][0]["symbol"] == "C1"
    assert req["max_cost_usd"] == "0.25"
    profile = empty_profile()
    profile["python_by_repo"]["quant-options"] = sys.executable
    work = tmp_path / "work"
    work.mkdir()
    with use_settings(profile):
        argv = command(req, work, work / "bundle")
    assert "--max-cost-usd" in argv and argv[-1] == "0.25"
    with pytest.raises(QuantStudioError):
        collection_request("bad", "options-research", "http://evil")
    rules.write_text(json.dumps([{"kind": "option"}]))
    with pytest.raises(QuantStudioError, match="费用"):
        collection_request(
            "bad",
            "options-research",
            "databento",
            str(rules),
            "2025-01-01",
            "2025-01-03",
            "authorized",
            "GLBX.MDP3",
            "NaN",
        )


def test_demo_request_needs_no_vendor_credentials(tmp_path):
    req = collection_request("demo", "global-futures-research", "demo")
    assert "contracts" not in req
    profile = empty_profile()
    profile["python_by_repo"]["quant-futures-global"] = sys.executable
    with use_settings(profile):
        argv = command(req, tmp_path, tmp_path / "bundle")
        assert "demo" in argv and "future" in argv
        with pytest.raises(QuantStudioError, match="未知"):
            command(req | {"key": "unwanted"}, tmp_path, tmp_path / "bundle")
