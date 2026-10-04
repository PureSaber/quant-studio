"""Saved onboarding must select the same input and Python after restart."""

import importlib.util
import json
import os
import sys
from pathlib import Path

from quant_studio.runner import preflight, run
from quant_studio.settings import use_settings
from quant_studio.setup import SettingsStore, empty_profile
from quant_studio.templates import Template, load_template


def test_hk_saved_setup_native_preflight_and_same_parameter_run(tmp_path, monkeypatch):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    spec = importlib.util.spec_from_file_location(
        "hk_setup_fixture", repo / "tests/test_research.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    case = fixture.case.__wrapped__()
    snapshot = fixture.snapshot(tmp_path, case)
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in snapshot.rglob("*")
        if p.is_file()
    }
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", "invalid-inherited-workspace")
    monkeypatch.setenv("QUANT_HK_SNAPSHOT", "invalid-inherited-input")
    profile = empty_profile()
    profile["environment"] = {
        "QUANT_WORKSPACE_ROOT": str(repo.parent),
        "QUANT_HK_SNAPSHOT": str(snapshot),
    }
    profile["python_by_repo"] = {"quant-hk-equity": sys.executable}
    store = SettingsStore(tmp_path / "settings.json")
    token, checks = store.review(profile)
    assert ("港股日频研究", "可运行") in checks
    assert not store.path.exists()
    store.save(token)
    restored = SettingsStore(store.path)
    template = load_template("hk-equity-daily")
    template = Template(template.metadata, case[0], template.directory)
    with use_settings(restored.snapshot()):
        checked = preflight(template, runs_root=tmp_path / "checks")
        completed = run(template, execute=True, runs_root=tmp_path / "runs")
    assert checked.status == "checked"
    assert completed.status == "succeeded"
    assert (checked.run_dir / "config.json").read_bytes() == (
        completed.run_dir / "config.json"
    ).read_bytes()
    request = json.loads((completed.run_dir / "request.json").read_text())
    assert Path(request["snapshot"]) == snapshot
    assert before == {
        p: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in snapshot.rglob("*")
        if p.is_file()
    }
