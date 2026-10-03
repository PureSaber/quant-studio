import json
import subprocess
import sys
from dataclasses import replace

import pytest

from quant_studio import QuantStudioError
from quant_studio.accounts import account_sources, inspect_source
from quant_studio.desk import list_runs
from quant_studio.runner import preview
from quant_studio.server import render_accounts, render_overview, render_results


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.delenv("QUANT_STUDIO_ACCOUNTS", raising=False)
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)


def configure(tmp_path, monkeypatch):
    root = tmp_path / "账户 # one"
    root.mkdir()
    config = tmp_path / "accounts.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.accounts/v1",
                "accounts": [{"id": "one", "label": "<账户>", "path": str(root)}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_STUDIO_ACCOUNTS", str(config))
    return config, root


def inspection():
    return {
        "schema_version": "quant.research-paper-inspection/v1",
        "read_only": True,
        "verification": "saved_registration_and_observation_artifacts",
        "account_id": "forward-one",
        "created_at": "2026-10-01T12:00:00+08:00",
        "checked_at": "2026-10-03T12:00:00+08:00",
        "definition_sha256": "a" * 64,
        "state": "pending",
        "window": {"start": "2026-10-08", "end": "2026-12-31"},
        "observations": [],
        "attempts": [],
        "latest": None,
    }


def test_no_sources_is_an_actionable_empty_state(tmp_path):
    assert account_sources() == []
    page = render_accounts()
    assert "尚未配置账户" in page and "QUANT_STUDIO_ACCOUNTS" in page
    home = render_overview(tmp_path)
    for link in ("/environment", "/data", "/strategy", "/accounts", "/results"):
        assert f'href="{link}"' in home
    assert "未设置 QUANT_WORKSPACE_ROOT" in home
    assert "环境就绪不代表数据完整" in home
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["json", "schema", "relative", "id", "duplicate"])
def test_invalid_account_configuration_is_not_silently_ignored(
    tmp_path, monkeypatch, failure
):
    path, _ = configure(tmp_path, monkeypatch)
    value = json.loads(path.read_text())
    if failure == "schema":
        value["schema_version"] = "other"
    elif failure == "relative":
        value["accounts"][0]["path"] = "../outside"
    elif failure == "id":
        value["accounts"][0]["id"] = "../outside"
    elif failure == "duplicate":
        value["accounts"].append(value["accounts"][0])
    path.write_text("{" if failure == "json" else json.dumps(value))
    with pytest.raises(QuantStudioError):
        account_sources()
    assert "账户核验未完成" in render_accounts()


def test_account_inspection_uses_declared_isolated_python_and_read_only_command(
    tmp_path, monkeypatch
):
    _, root = configure(tmp_path, monkeypatch)
    calls = []
    python = "selected-maintenance-python"
    monkeypatch.setattr("quant_studio.accounts.configured_python", lambda _: python)

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(inspection()), "")

    monkeypatch.setattr("quant_studio.accounts.subprocess.run", execute)
    source = account_sources()[0]
    result = inspect_source(source)
    assert result["latest"] is None and result["inspection_python"] == python
    assert calls[0][0] == [
        python,
        "-I",
        "-B",
        "-X",
        "utf8",
        "-m",
        "quant_pipeline.research_paper",
        "inspect",
        str(root),
    ]
    assert calls[0][1]["shell"] is False and calls[0][1]["timeout"] == 30
    assert not list(root.iterdir())
    page = render_accounts("one")
    assert "尚未到观察起点" in page and "收益与回撤不可用" in page
    assert "&lt;账户&gt;" in page and "0.00%" not in page
    calls.clear()
    assert "账户未配置" in render_accounts("../../other")
    assert calls == []
    with pytest.raises(QuantStudioError, match="不存在"):
        inspect_source(replace(source, path=root / "missing"))


@pytest.mark.parametrize("failure", ["exit", "json", "contract", "timeout"])
def test_inspection_failure_never_becomes_a_successful_account(
    tmp_path, monkeypatch, failure
):
    configure(tmp_path, monkeypatch)

    def execute(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        value = inspection()
        if failure == "contract":
            value["read_only"] = False
        return subprocess.CompletedProcess(
            argv,
            2 if failure == "exit" else 0,
            "broken" if failure == "json" else json.dumps(value),
            "hash mismatch",
        )

    monkeypatch.setattr("quant_studio.accounts.subprocess.run", execute)
    page = render_accounts("one")
    assert "账户核验未完成" in page
    assert "已核验保存的登记与观测产物" not in page


def test_failed_attempt_is_visible_and_escaped(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    value = inspection()
    value.update(
        state="observing",
        inspection_python=sys.executable,
        latest={"as_of": "2026-10-08", "metrics": {"total_return": -0.01}},
        attempts=[{"status": "failed", "as_of": "2026-10-08", "error": "<bad>"}],
    )
    monkeypatch.setattr("quant_studio.server.inspect_source", lambda _: value)
    page = render_accounts("one")
    assert "-1.00%" in page and "&lt;bad&gt;" in page and "不可用" in page


def test_report_only_nested_result_is_visible_but_escape_is_not(tmp_path):
    run = preview("synthetic-demo", runs_root=tmp_path)
    folder = run.run_dir / "strategy-output"
    folder.mkdir()
    (folder / "report.html").write_text("report")
    result = run.as_json()
    result.update(status="succeeded", report="strategy-output/report.html")
    path = run.run_dir / "result.json"
    path.write_text(json.dumps(result))
    assert list_runs(tmp_path)[0]["has_report"] == "1"
    assert f"/runs/{run.run_id}" in render_results(tmp_path)
    result["report"] = "../report.html"
    (tmp_path / "report.html").write_text("outside")
    path.write_text(json.dumps(result))
    assert list_runs(tmp_path)[0]["has_report"] == "0"
