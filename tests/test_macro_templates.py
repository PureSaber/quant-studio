import pytest

from quant_studio.setup import empty_profile, repositories, validate_shape
from quant_studio.templates import load_template, render_template, template_ids


@pytest.mark.parametrize("line", ["fx", "rates", "credit"])
def test_macro_templates_have_explicit_native_contract_and_distinct_inputs(line):
    name = f"macro-{line}-research"
    assert name in template_ids()
    template = load_template(name)
    assert template.workspace_repo == "quant-futures-spread"
    assert template.runtime_key == "quant-macro"
    assert template.preflight_contract["schema_version"] == "quant.macro.preflight/v1"
    assert template.preflight_contract["instrument_ids_required"]
    assert template.result_evidence["values"] == ["synthetic", "retrospective"]
    assert template.input_source["required_files"] == ["manifest.json", "data.json"]
    assert render_template(template).config["line"] == line
    profile = empty_profile()
    validate_shape(profile)
    assert "quant-macro" in repositories()


@pytest.mark.parametrize("name", ["fx-futures-research", "rates-futures-research"])
def test_futures_presets_gate_real_product_units_before_existing_engine(name):
    template = load_template(name)
    existing = load_template("global-futures-research")
    assert "quant_macro.futures_cli" in template.argv
    assert "quant_macro.futures_cli" in template.preflight_argv
    assert template.runtime_key == "quant-macro-futures"
    assert template.preflight_contract == existing.preflight_contract
    assert render_template(template).config["product"] in {"6E", "ZN"}


@pytest.mark.parametrize(
    "name,line",
    [
        ("macro-fx-portfolio", "fx_portfolio"),
        ("macro-bond-replay", "bond_replay"),
        ("macro-etf-relative-value", "bond_replay"),
        ("macro-reference-history", "reference_history"),
    ],
)
def test_extended_macro_templates_render_and_have_output_tables(name, line):
    t = load_template(name)
    assert render_template(t).config["line"] == line
    assert t.runtime_key == "quant-macro"
    assert t.preflight_contract["status_field"] == "software_preflight"
    assert "report.html" in t.result_files
    assert t.upstream_ref == load_template("macro-fx-research").upstream_ref
