import json
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.module_tools import initialize, register


def test_scaffold_and_register_validate_before_publish(tmp_path, monkeypatch):
    project = tmp_path / "my-research"
    initialize(project, "custom-my-research")
    assert (project / "my_research.py").is_file()
    registry = tmp_path / "registry"
    record = register(project, registry, python=sys.executable, install=False)
    assert record["id"] == "custom-my-research"
    assert record["preflight"]["software_preflight"] == "pass"
    from quant_studio.runner import _validate_preflight
    from quant_studio.templates import load_template

    monkeypatch.setenv("QUANT_STUDIO_TEMPLATES", str(registry))
    _validate_preflight(load_template("custom-my-research"), record["preflight"])
    target = registry / "custom-my-research/template.json"
    assert (
        json.loads(target.read_text(encoding="utf-8"))["workspace_repo"]
        == "my-research"
    )
    metadata = project / ".studio/template.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["argv"] = ["cmd", "/c", "anything"]
    metadata.write_text(json.dumps(value))
    with pytest.raises(QuantStudioError):
        register(project, registry, python=sys.executable, install=False)
    assert json.loads(target.read_text(encoding="utf-8"))["argv"][:2] == [
        "python",
        "-m",
    ]
