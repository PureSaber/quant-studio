import json
import subprocess
import sys

import pytest
import yaml

from quant_studio.runner import preflight, preview, template_readiness
from quant_studio.server import render_run
from quant_studio.settings import use_settings
from quant_studio.templates import load_template


def test_ashare_selected_cache_is_kept_between_preflight_and_run(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "a-share-multifactor").mkdir(parents=True)
    source = tmp_path / "existing cache"
    source.mkdir()
    original = source / "original.parquet"
    original.write_bytes(b"input identity")
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(
        "quant_studio.runner._executable", lambda *_a, **_k: sys.executable
    )
    monkeypatch.setattr("importlib.util.find_spec", lambda *_a: object())
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"software_preflight": "pass", "read_only": True}), ""
        )

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    checked = preflight(
        "a-share-four-factor", snapshot=source, runs_root=tmp_path / "checks"
    )
    planned = preview(
        "a-share-four-factor", snapshot=source, runs_root=tmp_path / "runs"
    )
    assert checked.status == "checked"
    for argv in [calls[0], planned.argv]:
        assert argv[argv.index("--data-dir") + 1] == str(source)
    configs = []
    for result in [checked, planned]:
        config = yaml.safe_load((result.run_dir / "config.yaml").read_text())
        assert config.pop("outputs_dir") == str(result.run_dir / "strategy-output")
        configs.append(config)
    assert configs[0] == configs[1]
    assert json.loads((checked.run_dir / "request.json").read_text())[
        "snapshot"
    ] == str(source)
    assert str(source) in render_run(checked.run_dir)
    assert original.read_bytes() == b"input identity"
    assert list(source.iterdir()) == [original]


@pytest.mark.parametrize("selected", [None, "missing"])
def test_ashare_saved_workspace_requires_existing_cache(
    tmp_path, monkeypatch, selected
):
    (tmp_path / "a-share-multifactor").mkdir()
    monkeypatch.setattr(
        "quant_studio.runner._executable", lambda *_a, **_k: sys.executable
    )
    monkeypatch.setattr("importlib.util.find_spec", lambda *_a: object())
    profile = {
        "schema_version": "quant-studio.settings/v1",
        "environment": {"QUANT_WORKSPACE_ROOT": str(tmp_path)},
        "python_by_repo": {},
    }
    if selected:
        profile["environment"]["QUANT_ASHARE_DATA_ROOT"] = str(tmp_path / selected)
    with use_settings(profile):
        ready = template_readiness(load_template("a-share-four-factor"))
    assert not ready.runnable and ready.needs_input


def test_ashare_cli_keeps_repository_data_default(tmp_path, monkeypatch):
    source = tmp_path / "a-share-multifactor/data"
    source.mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    planned = preview("a-share-four-factor", runs_root=tmp_path / "runs")
    assert planned.argv[planned.argv.index("--data-dir") + 1] == str(source)
