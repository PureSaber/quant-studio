import json
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.runner import preview, run
from quant_studio.templates import load_template


def test_preview_writes_artifacts_without_starting_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not call subprocess")

    monkeypatch.setattr("subprocess.run", forbidden)
    result = preview(
        "a-share-four-factor",
        {"rebalance_freq": "daily", "symbols_limit": 12},
        runs_root=tmp_path,
    )

    assert result.status == "previewed"
    assert (result.run_dir / "request.json").is_file()
    assert (result.run_dir / "config.yaml").is_file()
    command = json.loads((result.run_dir / "command.json").read_text("utf-8"))
    assert command["argv"][-2:] == ["--symbols-limit", "12"]


def test_external_run_is_blocked_without_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("blocked run must not call subprocess")
        ),
    )

    result = run(
        "paper-sim",
        {"initial_capital": 20000},
        execute=True,
        runs_root=tmp_path,
    )

    assert result.status == "blocked"
    assert result.report is None
    assert result.message is not None
    assert "quant-paper-sim" in result.message
    assert "只能预览" in result.message


def test_external_failure_saves_output_and_has_no_report(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    repo = workspace / "quant-paper-sim"
    repo.mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = [
        sys.executable,
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)",
    ]

    result = run(
        template,
        {},
        execute=True,
        runs_root=tmp_path / "runs",
    )

    assert result.status == "failed"
    assert result.returncode == 7
    assert result.report is None
    assert (result.run_dir / "stdout.txt").read_text("utf-8").strip() == "out"
    assert (result.run_dir / "stderr.txt").read_text("utf-8").strip() == "err"


def test_external_success_only_records_report_from_run_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    repo = workspace / "quant-paper-sim"
    repo.mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys; "
            "Path(sys.argv[1]).parent.joinpath('report.html').write_text('ok')"
        ),
        "{config}",
    ]

    result = run(
        template,
        {},
        execute=True,
        runs_root=tmp_path / "runs",
    )

    assert result.status == "succeeded"
    assert result.report == "report.html"


def test_hk_preview_does_not_create_upstream_output_directories(tmp_path):
    result = preview("hk-equity-daily", {}, runs_root=tmp_path)

    assert not (result.run_dir / "snapshot").exists()
    assert not (result.run_dir / "strategy-output").exists()
    assert str(result.run_dir / "snapshot") in result.argv
    assert str(result.run_dir / "strategy-output") in result.argv


def test_existing_run_directory_is_never_overwritten(tmp_path):
    existing = tmp_path / "fixed"
    existing.mkdir()

    with pytest.raises(QuantStudioError, match="fixed"):
        preview("synthetic-demo", {}, runs_root=tmp_path, run_id="fixed")


def test_report_name_cannot_escape_run_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["report_name"] = "../outside.html"
    template.metadata["argv"] = [sys.executable, "-c", "pass"]

    with pytest.raises(QuantStudioError, match="outside"):
        run(template, {}, execute=True, runs_root=tmp_path / "runs")


def test_external_timeout_is_configurable_and_recorded(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = [
        sys.executable,
        "-c",
        "import time; time.sleep(0.2)",
    ]

    result = run(
        template,
        {},
        execute=True,
        timeout=0.01,
        runs_root=tmp_path / "runs",
    )

    assert result.status == "failed"
    assert result.returncode is None
    assert "超时" in (result.run_dir / "stderr.txt").read_text("utf-8")


def test_workspace_repo_cannot_escape_workspace_root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["workspace_repo"] = "../outside"

    with pytest.raises(QuantStudioError, match="workspace"):
        run(template, {}, execute=True, runs_root=tmp_path / "runs")


def test_success_copies_upstream_nav_and_report(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    output = workspace / "a-share-multifactor" / "outputs" / "four_factors" / "latest"
    output.mkdir(parents=True)
    (output / "cumulative_returns.csv").write_text(
        ",Q5\n2024-01-02,1.00\n2024-01-03,1.10\n2024-01-04,0.90\n",
        encoding="utf-8",
    )
    (output / "report.html").write_text("<p>upstream</p>", encoding="utf-8")
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("a-share-four-factor")
    template.metadata["argv"] = [sys.executable, "-c", "raise SystemExit(0)"]

    result = run(template, {}, execute=True, runs_root=tmp_path / "runs")

    assert result.status == "succeeded"
    assert result.report == "report.html"
    assert "0.90" in (result.run_dir / "nav.csv").read_text(encoding="utf-8")
    assert "upstream" in (result.run_dir / "report.html").read_text(encoding="utf-8")
