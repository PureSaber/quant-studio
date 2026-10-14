from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant_studio import QuantStudioError
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from quant_studio.research_cards import FIELDS, DecisionStore, validate_card
from quant_studio.runner import run


def card():
    return {
        **{key: "明确的研究协议" for key in FIELDS},
        "sample_role": "retrospective",
        "start": "2025-01-02",
        "end": "2025-12-31",
    }


def test_card_revision_is_immutable_and_legacy_save_preserves_it(tmp_path):
    store = ProjectStore(tmp_path)
    old = store.save("研究", "问题")
    assert "research_card" not in old
    first = store.save(
        "研究",
        "问题",
        project_id=old["id"],
        expected=old["revision"],
        research_card=card(),
    )
    changed = {**card(), "rejection_rule": "新的否定条件"}
    second = store.save(
        "研究",
        "问题",
        project_id=old["id"],
        expected=first["revision"],
        research_card=changed,
    )
    assert store.get(old["id"], first["revision"])["research_card"] == card()
    third = store.save(
        "研究", "修改笔记", project_id=old["id"], expected=second["revision"]
    )
    assert third["research_card"] == changed
    with pytest.raises(QuantStudioError, match="更新"):
        store.save(
            "研究",
            "问题",
            project_id=old["id"],
            expected=first["revision"],
            research_card=card(),
        )


def test_cannot_relabel_past_sample_as_prospective():
    value = {**card(), "sample_role": "prospective"}
    with pytest.raises(QuantStudioError, match="未来区间"):
        validate_card(value)
    future = date.today() + timedelta(days=5)
    value.update(start=future.isoformat(), end=(future + timedelta(days=5)).isoformat())
    assert validate_card(value) == value
    with pytest.raises(QuantStudioError, match="字段"):
        validate_card({**value, "unknown": True})


def test_decision_retains_negative_result_and_detects_tampering(tmp_path):
    recipe = RecipeStore(tmp_path).save("基线", "synthetic-demo", {})
    store = ProjectStore(tmp_path)
    project = store.save(
        "研究",
        "问题",
        research_card=card(),
        recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
    )
    from quant_studio.recipes import recipe_template

    result = run(recipe_template(recipe), {}, execute=True, runs_root=tmp_path)
    decisions = DecisionStore(tmp_path)
    decision = decisions.append(
        project["id"],
        project["revision"],
        verdict="not_supported",
        action="stop_research",
        rationale="结果不支持假设，保留失败结论",
        evidence=[result.run_id],
    )
    assert decisions.list(project["id"])[0] == decision
    with pytest.raises(QuantStudioError, match="不属于"):
        decisions.append(
            project["id"],
            project["revision"],
            verdict="supported",
            action="continue_observation",
            rationale="依据",
            evidence=["other"],
        )
    target = tmp_path / result.run_id / "result.json"
    target.write_text(target.read_text() + " ")
    with pytest.raises(QuantStudioError, match="完整性"):
        decisions.list(project["id"])


def test_explicit_binding_does_not_leak_to_project_sharing_recipe(tmp_path):
    import json

    from quant_studio.recipes import recipe_template

    recipe = RecipeStore(tmp_path).save("共同方案", "synthetic-demo", {})
    store = ProjectStore(tmp_path)
    projects = [
        store.save(
            name,
            "问题",
            research_card=card(),
            recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
        )
        for name in ("甲", "乙")
    ]
    result = run(recipe_template(recipe), {}, execute=True, runs_root=tmp_path)
    jobs = tmp_path / ".jobs"
    jobs.mkdir()
    (jobs / "binding.json").write_text(
        json.dumps(
            {
                "run_id": result.run_id,
                "research_project": {
                    "id": projects[0]["id"],
                    "revision": projects[0]["revision"],
                },
            }
        )
    )
    assert len(store.experiments(projects[0]["id"])) == 1
    assert not store.experiments(projects[1]["id"])
    project = deepcopy(projects[0])
    store.save(
        project["name"],
        "修改问题",
        project_id=project["id"],
        expected=project["revision"],
    )
    assert (
        store.experiments(project["id"])[0]["research_project"]["revision"]
        == project["revision"]
    )
