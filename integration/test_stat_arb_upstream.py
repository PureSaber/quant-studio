"""Run only where the private upstream has been explicitly checked out and installed."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quant_studio.nav import parse_nav_csv
from quant_studio.runner import preflight, run
from quant_studio.runtime import configured_python
from quant_studio.server import render_run


def fingerprint(root):
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def diagnostics(result):
    log = result.run_dir / "stderr.txt"
    return result.as_json(), log.read_text(encoding="utf-8") if log.exists() else ""


@pytest.mark.parametrize(
    "settings",
    [
        {"gross_budget_scale": 1, "cost_multiplier": 1},
        {"gross_budget_scale": 0.5, "cost_multiplier": 2},
    ],
)
def test_native_research_preflight_run_and_rejection(tmp_path, monkeypatch, settings):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    assert repo.name == "quant-stat-arb"
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    python = configured_python(repo.name) or sys.executable
    source = tmp_path / "original research"
    shutil.copytree(repo / "data/synthetic", source / "data/synthetic")
    (source / "configs").mkdir()
    config = source / "configs/synthetic.yaml"
    shutil.copyfile(repo / "configs/synthetic.yaml", config)
    before = fingerprint(source)
    checked = preflight(
        "stat-arb-research", settings, snapshot=config, runs_root=tmp_path / "checks"
    )
    assert checked.status == "checked", diagnostics(checked)
    assert checked.evidence_kind == "synthetic"
    assert not (checked.run_dir / "strategy-output").exists()
    evidence = json.loads(
        (checked.run_dir / "preflight.json").read_text(encoding="utf-8")
    )
    assert evidence["read_only"] is True and evidence["investable"] is False
    assert evidence["folds"] > 0
    page = render_run(checked.run_dir)
    assert "使用相同参数运行" in page and "不筛选配对" in page
    assert fingerprint(source) == before

    result = run(
        "stat-arb-research",
        settings,
        snapshot=config,
        execute=True,
        runs_root=tmp_path / "runs",
        timeout=240,
    )
    assert result.status == "succeeded", diagnostics(result)
    assert result.evidence_kind == "synthetic"
    assert result.report is None  # The native engine exports research CSVs, not HTML.
    assert (result.run_dir / "config.json").read_bytes() == (
        checked.run_dir / "config.json"
    ).read_bytes()
    output = result.run_dir / "strategy-output"
    context = json.loads((output / "run_context.json").read_text(encoding="utf-8"))
    assert (
        context["input_preflight"]["effective_config_sha256"]
        == evidence["effective_config_sha256"]
    )
    for name in ("config", "path", "total_return", "industry", "adv"):
        assert (
            context["input_preflight"]["input_files"][name]
            == evidence["input_files"][name]
        )
    metrics = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    nav = parse_nav_csv(result.run_dir / "nav.csv")
    assert nav.initial_nav == 1 and len(nav.rows) == metrics["scored_days"]
    assert float(nav.period_return) == pytest.approx(
        metrics["walk_forward_net_return"], abs=1e-12
    )
    # Native summary stores a signed drawdown; the UI shows its positive magnitude.
    assert float(nav.max_drawdown) == pytest.approx(-metrics["max_drawdown"], abs=1e-12)
    for name in ("positions.csv", "orders.csv", "costs.csv"):
        assert (result.run_dir / name).read_bytes() == (
            output / "standard" / name
        ).read_bytes()
    page = render_run(result.run_dir)
    assert "合成数据" in page and "未提供基准净值" in page
    assert "研究目标变动" in page and "研究费用（收益比例）" in page
    assert "账户金额" not in page
    verified = subprocess.run(
        [
            python,
            "-c",
            "import sys; from pathlib import Path; "
            "from quant_stat_arb.contract import validate_standard_run; "
            "validate_standard_run(Path(sys.argv[1]))",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert verified.returncode == 0, verified.stderr
    assert fingerprint(source) == before

    (source / "data/synthetic/prices.csv").write_text(
        "date,A\ninvalid,1\n", encoding="utf-8"
    )
    broken = fingerprint(source)
    rejected = preflight(
        "stat-arb-research", settings, snapshot=config, runs_root=tmp_path / "checks"
    )
    assert rejected.status == "check_failed", diagnostics(rejected)
    assert "使用相同参数运行" not in render_run(rejected.run_dir)
    failed = run(
        "stat-arb-research",
        settings,
        snapshot=config,
        execute=True,
        runs_root=tmp_path / "runs",
    )
    assert failed.status == "failed", diagnostics(failed)
    assert not (failed.run_dir / "nav.csv").exists()
    assert fingerprint(source) == broken
