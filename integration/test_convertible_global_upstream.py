"""Installed upstream packages, real CLI subprocesses and immutable report contracts."""

import json
import os
import shutil
import sys
from pathlib import Path

from quant_studio.recipes import RecipeStore, recipe_template
from quant_studio.runner import preflight, run
from quant_studio.settings import use_settings


def test_convertible_account_installed_upstream(tmp_path):
    from quant_execution.derivative_replay import verify_artifacts

    repo = Path(os.environ["QUANT_OPTIONS_REPO"])
    source = tmp_path / "input"
    source.mkdir()
    shutil.copyfile(repo / "examples/convertibles/replay.json", source / "input.json")
    profile = {
        "environment": {"QUANT_WORKSPACE_ROOT": str(repo.parent)},
        "python_by_repo": {"quant-options": sys.executable},
    }
    with use_settings(profile):
        checked = preflight(
            "convertible-account", {}, snapshot=source, runs_root=tmp_path / "runs"
        )
        assert checked.status == "checked", checked.as_json()
        result = run(
            "convertible-account",
            {},
            snapshot=source,
            runs_root=tmp_path / "runs",
            execute=True,
        )
        assert result.status == "succeeded", result.as_json()
    assert verify_artifacts(result.run_dir / "strategy-output")["status"] == "passed"
    study = json.loads((result.run_dir / "strategy-output/study.json").read_text())
    assert all(float(v) == 0 for v in study["final_positions"].values())
    assert (result.run_dir / "cashflows.csv").exists()


def test_global_portfolio_installed_upstream(tmp_path):
    from quant_data_kit.derivatives.demo import write_demo
    from quant_execution.derivative_replay import verify_artifacts

    repo = Path(os.environ["QUANT_FUTURES_REPO"])
    bundle = write_demo(tmp_path / "input", "future")
    profile = {
        "environment": {"QUANT_WORKSPACE_ROOT": str(repo.parent)},
        "python_by_repo": {"quant-futures-global": sys.executable},
    }
    with use_settings(profile):
        recipe = RecipeStore(tmp_path / "runs").save(
            "demo", "global-futures-portfolio", {}, snapshot=str(bundle.root)
        )
        template = recipe_template(recipe)
        checked = preflight(
            template, {}, snapshot=bundle.root, runs_root=tmp_path / "runs"
        )
        assert checked.status == "checked", checked.as_json()
        result = run(
            template,
            {},
            snapshot=bundle.root,
            runs_root=tmp_path / "runs",
            execute=True,
        )
        assert result.status == "succeeded", result.as_json()
    assert verify_artifacts(result.run_dir / "strategy-output")["status"] == "passed"
