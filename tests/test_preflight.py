import json
import subprocess
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.runner import Readiness, preflight
from quant_studio.server import render_run
from quant_studio.templates import load_template


@pytest.mark.parametrize("outcome", ["pass", "exit", "json", "contract", "timeout"])
def test_native_preflight_never_runs_strategy_and_keeps_failure_evidence(
    tmp_path, monkeypatch, outcome
):
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "quant-hk-equity").mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text("{}")
    template = load_template("hk-equity-daily")
    template.metadata["argv"][0] = "python"
    template.metadata["preflight_argv"][0] = "python"
    monkeypatch.setattr(
        "quant_studio.runner.template_readiness",
        lambda *_args, **_kw: Readiness(True, "ready", executable=sys.executable),
    )
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        value = {"software_preflight": "pass", "investable": False}
        if outcome == "contract":
            value["software_preflight"] = "unknown"
        return subprocess.CompletedProcess(
            argv,
            2 if outcome == "exit" else 0,
            "invalid" if outcome == "json" else json.dumps(value),
            "bad snapshot" if outcome == "exit" else "",
        )

    monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    result = preflight(
        template,
        {"initial_cash": "500000", "rebalance_sessions": 1},
        snapshot=snapshot,
        runs_root=tmp_path / "runs",
    )
    assert calls[0][1] == "preflight" and "run" not in calls[0]
    assert not (result.run_dir / "strategy-output").exists()
    assert not (result.run_dir / "nav.csv").exists()
    page = render_run(result.run_dir, csrf_token="same-session")
    if outcome == "pass":
        assert result.status == "checked"
        assert 'name="initial_cash" value="500000"' in page
        assert 'name="rebalance_sessions" value="1"' in page
        assert 'name="_csrf_token" value="same-session"' in page
        assert "使用相同参数运行" in page
    else:
        assert result.status == "check_failed"
        assert "数据预检未通过" in page
        assert "使用相同参数运行" not in page


def test_unsupported_preflight_has_no_run_side_effects(tmp_path):
    with pytest.raises(QuantStudioError, match="尚未接入"):
        preflight("a-share-four-factor", runs_root=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()
