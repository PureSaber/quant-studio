import json
import shutil

import pytest

from quant_studio import QuantStudioError
from quant_studio.templates import load_template, template_ids


def test_owner_registered_template_is_available_to_recipe_forms(tmp_path, monkeypatch):
    from pathlib import Path

    source = Path(__file__).parents[1] / "examples" / "templates" / "custom-example"
    shutil.copytree(source, tmp_path / "custom-example")
    monkeypatch.setenv("QUANT_STUDIO_TEMPLATES", str(tmp_path))
    template = load_template("custom-example")
    assert template.id in template_ids()
    assert template.argv[:3] == ["python", "-m", "quant_studio.example_research"]
    manifest = tmp_path / "custom-example" / "template.json"
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["base_config"] = "../outside.json"
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(QuantStudioError):
        load_template("custom-example")


def test_custom_registry_cannot_replace_builtin(tmp_path, monkeypatch):
    folder = tmp_path / "synthetic-demo"
    folder.mkdir()
    (folder / "template.json").write_text("{}")
    monkeypatch.setenv("QUANT_STUDIO_TEMPLATES", str(tmp_path))
    assert load_template("synthetic-demo").kind == "synthetic"
    assert template_ids().count("synthetic-demo") == 1
