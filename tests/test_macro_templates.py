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
def test_futures_presets_reuse_existing_engine_and_dependency_revision(name):
    template = load_template(name)
    existing = load_template("global-futures-research")
    assert template.argv == existing.argv
    assert template.preflight_argv == existing.preflight_argv
    assert template.runtime_key == existing.runtime_key
    assert template.upstream_ref == existing.upstream_ref
    assert render_template(template).config == render_template(existing).config
