"""Actual derivative CLIs: causal replay, analysis-only and corrupt input rejection."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quant_studio.nav import parse_nav_csv
from quant_studio.runner import preflight, run
from quant_studio.server import render_run
from quant_studio.settings import use_settings


@pytest.mark.parametrize("kind", ["global", "options"])
def test_derivative_native_contract(kind, tmp_path):
    from quant_data_kit.derivatives.demo import write_demo
    from quant_execution.derivative_replay import verify_artifacts

    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    option = kind == "options"
    template = "options-research" if option else "global-futures-research"
    runtime = "quant-options" if option else "quant-futures-global"
    source = write_demo(tmp_path / "source", "option" if option else "future").root
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    profile = {
        "environment": {"QUANT_WORKSPACE_ROOT": str(repo.parent)},
        "python_by_repo": {runtime: sys.executable},
    }
    with use_settings(profile):
        checked = preflight(template, {}, snapshot=source, runs_root=tmp_path / "runs")
        assert checked.status == "checked", checked.as_json()
        assert checked.evidence_kind == "synthetic"
        checked_page = render_run(checked.run_dir)
        assert "数据预检证据" in checked_page
        assert f"数据行数：{258 if option else 43}" in checked_page
        assert not (checked.run_dir / "strategy-output").exists()
        for mode in ["replay", "analysis"]:
            result = run(
                template,
                {"initial_cash": 54321, "mode": mode},
                snapshot=source,
                execute=True,
                runs_root=tmp_path / "runs",
            )
            assert result.status == "succeeded", result.as_json()
            output = result.run_dir / "strategy-output"
            assert verify_artifacts(output)["status"] == "passed"
            assert result.evidence_kind == "synthetic"
            assert "合成数据" in render_run(result.run_dir)
            study = json.loads((output / "study.json").read_text(encoding="utf-8"))
            assert study["config"]["initial_cash"] == 54321
            if mode == "replay":
                assert parse_nav_csv(result.run_dir / "nav.csv").initial_nav == 54321
            else:
                assert not (result.run_dir / "nav.csv").exists()
            module = "quant_options.cli" if option else "qfs_global.cli"
            verify = subprocess.run(
                [sys.executable, "-m", module, "verify", "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert verify.returncode == 0, verify.stderr
        assert {p.name: p.read_bytes() for p in source.iterdir()} == before
        (source / "quotes.csv").write_text("corrupt\n", encoding="utf-8")
        rejected = preflight(template, {}, snapshot=source, runs_root=tmp_path / "runs")
        assert rejected.status == "check_failed"
