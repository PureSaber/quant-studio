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


def test_old_workspace_outputs_are_never_used(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    output = workspace / "a-share-multifactor" / "outputs" / "four_factors" / "latest"
    output.mkdir(parents=True)
    (output / "cumulative_returns.csv").write_text(
        ",Q5\n2024-01-02,1.00\n2024-01-03,1.10\n2024-01-04,0.90\n",
        encoding="utf-8",
    )
    (output / "report.html").write_text("<p>upstream</p>", encoding="utf-8")
    (output / "holdings.csv").write_text(
        "symbol,weight\n000001,0.5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("a-share-four-factor")
    template.metadata["argv"] = [sys.executable, "-c", "raise SystemExit(0)"]

    result = run(template, {}, execute=True, runs_root=tmp_path / "runs")

    assert result.status == "failed"
    assert result.report is None
    assert not (result.run_dir / "nav.csv").exists()
    assert not (result.run_dir / "holdings.csv").exists()


def test_current_run_outputs_are_isolated_and_collected(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "a-share-multifactor").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("a-share-four-factor")
    # Generic external-output discovery; the real A-share contract is tested separately.
    template.metadata.pop("result_files")
    template.metadata.pop("nav_column")
    template.metadata["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys, yaml; "
        "p=Path(yaml.safe_load(Path(sys.argv[1]).read_text())['outputs_dir'])/'new'; "
        "p.mkdir(parents=True); "
        "(p/'nav.csv').write_text('date,nav\\n2026-01-01,1.0040\\n"
        "2026-01-02,1.0049\\n'); "
        "(p/'report.html').write_text('current'); "
        "(p/'holdings.csv').write_text('symbol,weight\\nABC,0.5\\n')",
        "{config}",
    ]
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "succeeded"
    assert "1.0049" in (result.run_dir / "nav.csv").read_text()
    assert result.report == "strategy-output/new/report.html"
    assert (result.run_dir / result.report).read_text() == "current"
    assert "ABC" in (result.run_dir / "holdings.csv").read_text()


def test_missing_executable_blocks_before_start(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = ["nonexistent-quant-command-12345"]
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "blocked"
    assert "nonexistent-quant-command" in result.message
    assert (result.run_dir / "result.json").is_file()


def test_startup_oserror_is_recorded(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = [sys.executable, "-c", "pass"]

    def fail(*args, **kwargs):
        raise FileNotFoundError("executable disappeared")

    monkeypatch.setattr("subprocess.run", fail)
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "failed"
    assert "executable disappeared" in (result.run_dir / "stderr.txt").read_text(
        "utf-8"
    )
    assert (
        json.loads((result.run_dir / "result.json").read_text())["status"] == "failed"
    )


def test_missing_python_module_blocks(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "quant-paper-sim").mkdir(parents=True)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    template = load_template("paper-sim")
    template.metadata["argv"] = ["python", "-m", "nonexistent_quant_module_12345"]
    result = run(template, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "blocked"
    assert result.argv[0] == sys.executable
