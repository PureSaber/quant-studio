import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore


def test_project_versions_pin_recipes_and_reject_stale_edits(tmp_path):
    recipes = RecipeStore(tmp_path)
    recipe = recipes.save("基线", "synthetic-demo", {})
    store = ProjectStore(tmp_path)
    first = store.save(
        "成交量研究",
        "成交量与波动率的关系",
        recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
    )
    second = store.save(
        "成交量研究",
        "加入对照",
        project_id=first["id"],
        expected=first["revision"],
        recipes=first["recipes"],
    )
    assert store.get(first["id"], first["revision"])["question"] != second["question"]
    with pytest.raises(QuantStudioError, match="更新"):
        store.save("冲突", "问题", project_id=first["id"], expected=first["revision"])
    recipes.save(
        "基线",
        "synthetic-demo",
        {"initial_capital": 20000},
        recipe_id=recipe["id"],
        expected=recipe["revision"],
    )
    assert store.get(first["id"])["recipes"][0]["revision"] == recipe["revision"]
    assert len(store.list(query="对照")) == 1


def test_project_rejects_unknown_sources_and_tampering(tmp_path):
    store = ProjectStore(tmp_path)
    with pytest.raises(QuantStudioError):
        store.save("研究", "问题", datasets=["a" * 32])
    with pytest.raises(QuantStudioError):
        store.get("../outside")
    record = store.save("研究", "问题")
    path = store.root / record["id"] / (record["revision"] + ".json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["question"] = "tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(QuantStudioError, match="完整"):
        store.get(record["id"])
