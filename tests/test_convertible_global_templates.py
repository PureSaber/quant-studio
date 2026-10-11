import pytest

from quant_studio.templates import load_template, render_template


@pytest.mark.parametrize(
    "template_id,module",
    [
        ("convertible-account", "quant_options.convertibles"),
        ("global-futures-portfolio", "qfs_global.workbench"),
        ("global-index-basis", "qfs_global.workbench"),
        ("commodity-reference-history", "qfs_global.workbench"),
    ],
)
def test_research_templates_have_executable_contracts(template_id, module):
    template = load_template(template_id)
    assert template.argv[:3] == ["python", "-m", module]
    assert template.preflight_argv[3] == "preflight"
    assert template.requires_snapshot
    assert template.input_source["required_files"]
    assert "artifacts.json" in template.result_files
    render_template(template)


def test_vendor_reference_stays_distinct_from_contract_replay():
    template = load_template("commodity-reference-history")
    assert template.result_evidence["values"] == ["retrospective_reference"]
    assert "nav.csv" not in template.result_files
    assert template.preflight_contract["positive_counts"] == ["rows"]
