import pytest

from quant_studio import QuantStudioError
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore, recipe_template
from quant_studio.research_cards import FIELDS, DecisionStore
from quant_studio.runner import run


def test_findings_retain_rejected_hypothesis_revision_and_check_evidence(tmp_path):
    from quant_studio.findings import findings_body

    recipe = RecipeStore(tmp_path).save("baseline", "synthetic-demo", {})
    store = ProjectStore(tmp_path)
    card = {
        **dict.fromkeys(FIELDS, "old hypothesis"),
        "sample_role": "retrospective",
        "start": "2025-01-01",
        "end": "2025-12-31",
    }
    project = store.save(
        "<study>",
        "question",
        research_card=card,
        recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
    )
    result = run(recipe_template(recipe), {}, execute=True, runs_root=tmp_path)
    DecisionStore(tmp_path).append(
        project["id"],
        project["revision"],
        verdict="not_supported",
        action="stop_research",
        rationale="negative evidence",
        evidence=[result.run_id],
    )
    store.save(
        "new title",
        "new question",
        project_id=project["id"],
        expected=project["revision"],
        research_card={**card, "mechanism": "new hypothesis"},
    )
    page = findings_body(tmp_path, {"action": ["stop_research"]})
    assert "停止研究" in page and "不支持" in page and "negative evidence" in page
    assert "old hypothesis" in page and "new hypothesis" not in page
    assert "&lt;study&gt;" in page and "<study>" not in page
    assert result.run_id in page and project["revision"] in page
    assert "negative evidence" not in findings_body(
        tmp_path, {"action": ["revise_hypothesis"]}
    )
    path = result.run_dir / "result.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(QuantStudioError, match="完整性"):
        findings_body(tmp_path, {})


def test_empty_findings_have_no_invented_positive_decisions(tmp_path):
    from quant_studio.findings import findings_body

    ProjectStore(tmp_path).save("pending", "question")
    page = findings_body(tmp_path, {})
    assert "尚未记录结论" in page
    assert "pending" in page
