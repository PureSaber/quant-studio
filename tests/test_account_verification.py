import json
import subprocess
import sys
from pathlib import Path

import pytest

from quant_studio import QuantStudioError
from quant_studio.desk import list_runs
from quant_studio.runner import run
from quant_studio.server import render_run
from quant_studio.templates import load_template


@pytest.mark.parametrize(
    "mode", ["pass", "nonzero", "invalid_json", "missing", "wrong_type"]
)
def test_native_verification_controls_success_and_chart_visibility(
    tmp_path, monkeypatch, mode
):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = [sys.executable, "-c", "strategy", "{config}"]
    template.metadata["verification_argv"] = [sys.executable, "-c", "verify"]
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv[2])
        if argv[2] == "strategy":
            directory = Path(argv[3]).parent
            (directory / "nav.csv").write_text(
                "date,nav\n2025-01-01,100000\n2025-01-02,100100\n"
            )
            return subprocess.CompletedProcess(argv, 0, "completed", "")
        assert calls == ["strategy", "verify"]
        evidence = {"replay_verified": True, "projection_status": "complete"}
        if mode == "nonzero":
            return subprocess.CompletedProcess(
                argv, 2, "", "cash reconciliation failed"
            )
        if mode == "missing":
            evidence.pop("replay_verified")
        elif mode == "wrong_type":
            evidence["replay_verified"] = 1
        stdout = "{unfinished" if mode == "invalid_json" else json.dumps(evidence)
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    page = render_run(result.run_dir)
    if mode == "pass":
        assert result.status == "succeeded" and result.verification_status == "pass"
        assert "账本重放校验通过" in page and "净值曲线" in page
        (result.run_dir / "verification.json").write_text("{}")
        damaged = render_run(result.run_dir)
        assert "账本核验未通过" in damaged and "净值曲线" not in damaged
    else:
        assert result.status == "failed" and result.verification_status == "failed"
        assert "账本核验未通过" in page and "净值曲线" not in page
        assert list_runs(tmp_path / "runs")[0]["has_nav"] == "0"
        assert (result.run_dir / "nav.csv").is_file()


@pytest.mark.parametrize("failure", ["process", "collection"])
@pytest.mark.parametrize("nav", ["date,nav\n2025-01-01,100000\n", "unfinished CSV"])
def test_failed_run_keeps_partial_artifacts_without_presenting_results(
    tmp_path, monkeypatch, failure, nav
):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = [sys.executable, "-c", "strategy", "{config}"]
    template.metadata.pop("verification_argv")

    def execute(argv, **kwargs):
        directory = Path(argv[3]).parent
        (directory / "nav.csv").write_text(nav, encoding="utf-8")
        (directory / "orders.csv").write_text("order_id\npartial-order\n")
        return subprocess.CompletedProcess(
            argv, 8 if failure == "process" else 0, "", "strategy interrupted"
        )

    def collect(*args, **kwargs):
        raise QuantStudioError("incomplete output")

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    monkeypatch.setattr("quant_studio.runner.collect_outputs", collect)
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "failed"
    before = (result.run_dir / "nav.csv").read_bytes()
    page = render_run(result.run_dir)
    assert "failed" in page
    assert (
        "strategy interrupted" if failure == "process" else "incomplete output"
    ) in page
    assert "净值曲线" not in page and "partial-order" not in page
    assert list_runs(tmp_path / "runs")[0]["has_nav"] == "0"
    assert (result.run_dir / "nav.csv").read_bytes() == before
