"""Actual native timing preflight, publication gates and verified views."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from quant_studio import QuantStudioError
from quant_studio.nav import parse_nav_csv
from quant_studio.runner import preflight, run
from quant_studio.runtime import configured_python
from quant_studio.server import render_run


def fingerprints(root):
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "case,factor", [("default", 1), ("cost", 2), ("hold", 1), ("blocked", 1)]
)
def test_timing_native_preflight_run_publication_and_tamper(
    tmp_path, monkeypatch, case, factor
):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    assert repo.name == "quant-timing"
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(repo / "tests/fixtures/sleeves.csv", source / "prices.csv")
    config = yaml.safe_load(
        (repo / "configs/combined.yaml").read_text(encoding="utf-8")
    )
    config["input"]["path"] = "prices.csv"
    config["source"] = {"evidence_kind": "synthetic"}
    if case == "hold":
        config["macro"] = {
            "context": "macro.json",
            "incomplete_policy": "hold_previous",
        }
        (source / "macro.json").write_text(
            json.dumps({"complete": False, "values": {}})
        )
    if case == "blocked":
        config["position"]["vol_lookback"] = 10000
    path = source / "research.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = fingerprints(source)
    settings = {"cost_multiplier": factor}
    checked = preflight(
        "timing-research", settings, snapshot=path, runs_root=tmp_path / "checks"
    )
    assert checked.status == "checked", checked.as_json()
    assert not (checked.run_dir / "strategy-output").exists()
    evidence = json.loads(
        (checked.run_dir / "preflight.json").read_text(encoding="utf-8")
    )
    assert evidence["effective_config"]["costs"]["bps"] == 10 * factor
    assert fingerprints(source) == before
    result = run(
        "timing-research",
        settings,
        snapshot=path,
        execute=True,
        runs_root=tmp_path / "runs",
        timeout=120,
    )
    assert result.status == ("failed" if case == "blocked" else "succeeded"), (
        result.as_json()
    )
    output = result.run_dir / "strategy-output"
    view = json.loads((output / "studio-view/view.json").read_text(encoding="utf-8"))
    assert view["effective_config_sha256"] == evidence["effective_config_sha256"]
    metrics = json.loads((output / "standard/metrics.json").read_text(encoding="utf-8"))
    context = metrics["input_context"]["input_checks"]
    for key in ("config", "input.path"):
        assert context["input_files"][key] == evidence["input_files"][key]
    assert (result.run_dir / "config.json").read_bytes() == (
        checked.run_dir / "config.json"
    ).read_bytes()
    assert view["verified_artifacts"] == 6
    assert (output / "studio-view/folds.csv").read_bytes() == (
        output / "validation/fold_metrics.csv"
    ).read_bytes()
    page = render_run(result.run_dir)
    assert "描述性全区间收益" in page and "滚动样本外结果" in page
    assert "平均折收益不等于复合收益" in page
    if case == "blocked":
        assert "仓位发布被阻断" in page
        assert not (result.run_dir / "nav.csv").exists()
    else:
        assert page.index("策略净值 · 描述性全区间") < page.index(
            "基准净值 · 描述性全区间"
        )
        nav = parse_nav_csv(result.run_dir / "nav.csv")
        baseline = parse_nav_csv(result.run_dir / "benchmark_nav.csv")
        assert nav.initial_nav == baseline.initial_nav == 1
        assert float(nav.period_return) == pytest.approx(
            metrics["descriptive_full_sample_net"], abs=1e-12
        )
        assert float(baseline.period_return) == pytest.approx(
            metrics["descriptive_full_sample_benchmark"], abs=1e-12
        )
        assert [day for day, _ in nav.rows] == [day for day, _ in baseline.rows]
        for name in ("positions.csv", "orders.csv", "costs.csv"):
            assert (result.run_dir / name).read_bytes() == (
                output / "standard" / name
            ).read_bytes()
        assert (
            "保留旧仓位，未发布新系数" if case == "hold" else "已发布研究仓位系数"
        ) in page
    assert (output / "position_scale.json").exists() == (
        case not in {"hold", "blocked"}
    )
    assert fingerprints(source) == before

    if case == "default":
        for relative in (
            "nav.csv",
            "positions.csv",
            "strategy-output/standard/metrics.json",
            "strategy-output/decision.json",
            "strategy-output/validation/fold_metrics.csv",
            "strategy-output/studio-view/folds.csv",
            "strategy-output/studio-view/view.json",
        ):
            target = result.run_dir / relative
            original = target.read_bytes()
            target.write_bytes(
                original.replace(b"0", b"9", 1)
                if relative == "nav.csv"
                else original + b"\n"
            )
            with pytest.raises(QuantStudioError):
                render_run(result.run_dir)
            target.write_bytes(original)
        missing = output / "validation/fold_metrics.csv"
        saved = missing.read_bytes()
        missing.unlink()
        with pytest.raises(QuantStudioError, match="缺失"):
            render_run(result.run_dir)
        missing.write_bytes(saved)
        # Corruption must also fail before a new view can be published.
        damaged = tmp_path / "damaged"
        shutil.copytree(output, damaged, ignore=shutil.ignore_patterns("studio-view"))
        with (damaged / "validation/fold_metrics.csv").open("a") as handle:
            handle.write("\n")
        python = configured_python(repo.name) or sys.executable
        import quant_studio.timing_view as inspector

        verification = subprocess.run(
            [
                python,
                "-I",
                inspector.__file__,
                "--run",
                str(damaged),
                "--output",
                str(damaged / "studio-view"),
                "--project",
                "quant-timing",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert verification.returncode != 0 and "mutated" in verification.stderr
        assert not (damaged / "studio-view").exists()
        assert "已发布研究仓位系数" in render_run(result.run_dir)
