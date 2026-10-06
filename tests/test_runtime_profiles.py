import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from quant_studio import QuantStudioError
from quant_studio.runner import _executable, preview, run, template_readiness
from quant_studio.runtime import configured_python
from quant_studio.server import render_environment
from quant_studio.templates import load_template


def _configure(tmp_path, monkeypatch, executable, repository="quant-paper-sim"):
    profile = tmp_path / "runtimes.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {repository: str(executable)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_STUDIO_RUNTIMES", str(profile))
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / repository).mkdir(exist_ok=True)
    return profile


def test_separate_environment_is_used_for_preview_and_actual_execution(
    tmp_path, monkeypatch
):
    environment = tmp_path / "independent"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _configure(tmp_path, monkeypatch, python)
    template = load_template("paper-sim")
    template.metadata.pop(
        "verification_argv"
    )  # The fixture only tests interpreter selection.
    template.metadata["argv"] = [
        "python",
        "-c",
        "import sys; from pathlib import Path; print(sys.executable); "
        "Path(sys.argv[1]).parent.joinpath('report.html').write_text('verified')",
        "{config}",
    ]
    prepared = preview(template, runs_root=tmp_path / "runs")
    assert prepared.argv[0] == str(python)
    readiness = template_readiness(template)
    assert readiness.runnable and readiness.executable == str(python)
    completed = run(template, execute=True, runs_root=tmp_path / "runs")
    assert completed.status == "succeeded"
    assert Path((completed.run_dir / "stdout.txt").read_text().strip()) == python
    assert json.loads((completed.run_dir / "command.json").read_text())["argv"][
        0
    ] == str(python)


def test_module_probe_uses_selected_environment(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, sys.executable)
    template = load_template("paper-sim")
    template.metadata["argv"] = ["python", "-m", "json.tool"]
    assert template_readiness(template).runnable
    template.metadata["argv"][-1] = "quant_missing_module_for_profile_test"
    ready = template_readiness(template)
    assert not ready.runnable
    assert "quant_missing_module_for_profile_test" in ready.message


def test_selected_environment_never_borrows_cli_from_path(tmp_path, monkeypatch):
    environment = tmp_path / "selected"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "wrong-environment/quant-hk")
    assert _executable("quant-hk", python=str(python)) is None
    cli = python.parent / ("quant-hk.exe" if os.name == "nt" else "quant-hk")
    cli.touch()
    assert _executable("quant-hk", python=str(python)) == str(cli)


def test_selected_environment_uses_its_reported_scripts_directory(
    tmp_path, monkeypatch
):
    python = tmp_path / "python.exe"
    python.touch()
    scripts = tmp_path / "console-scripts"
    scripts.mkdir()
    name = "quant-hk.exe" if os.name == "nt" else "quant-hk"
    (python.parent / name).touch()

    def probe(argv, **kwargs):
        assert argv[0] == str(python) and "sysconfig" in argv[-1]
        return subprocess.CompletedProcess(argv, 0, json.dumps(str(scripts)), "")

    monkeypatch.setattr("quant_studio.runner.subprocess.run", probe)
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "wrong-environment/quant-hk")
    assert _executable("quant-hk", python=str(python)) is None
    cli = scripts / name
    cli.touch()
    assert _executable("quant-hk", python=str(python)) == str(cli)


@pytest.mark.parametrize(
    "failure", ["exit-code", "invalid-json", "relative", "timeout"]
)
def test_failed_scripts_probe_cannot_borrow_a_global_cli(
    tmp_path, monkeypatch, failure
):
    def probe(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 10)
        return subprocess.CompletedProcess(
            argv,
            1 if failure == "exit-code" else 0,
            "broken" if failure == "invalid-json" else json.dumps("relative/scripts"),
            "",
        )

    monkeypatch.setattr("quant_studio.runner.subprocess.run", probe)
    monkeypatch.setattr("shutil.which", lambda *a, **kw: "wrong-environment/quant-hk")
    assert _executable("quant-hk", python=str(tmp_path / "python.exe")) is None


@pytest.mark.parametrize(
    "failure", ["missing-file", "invalid-json", "relative-python", "missing-python"]
)
def test_invalid_profile_is_actionable_and_cannot_silently_fallback(
    tmp_path, monkeypatch, failure
):
    profile = _configure(tmp_path, monkeypatch, sys.executable)
    if failure == "missing-file":
        profile.unlink()
    elif failure == "invalid-json":
        profile.write_text("broken", encoding="utf-8")
    elif failure == "relative-python":
        _configure(tmp_path, monkeypatch, "python")
    else:
        _configure(tmp_path, monkeypatch, tmp_path / "absent-python.exe")
    with pytest.raises(QuantStudioError):
        configured_python("quant-paper-sim")
    ready = template_readiness(load_template("paper-sim"))
    assert not ready.runnable and ready.message
    assert template_readiness(load_template("synthetic-demo")).runnable


def test_environment_page_exposes_actual_command_and_profile_instructions(
    tmp_path, monkeypatch
):
    _configure(tmp_path, monkeypatch, sys.executable, "a-share-multifactor")
    page = render_environment()
    assert "执行环境" in page and "QUANT_STUDIO_RUNTIMES" in page
    assert sys.executable in page
