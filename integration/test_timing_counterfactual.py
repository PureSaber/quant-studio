"""Actual native fixed families, read-only inputs, downloads and rejected corruption."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from quant_studio import QuantStudioError
from quant_studio.runner import preflight, run
from quant_studio.server import render_run, resolve_run_asset


def fingerprints(root):
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("case", ["position", "style", "failure"])
def test_timing_counterfactual_native_family(tmp_path, monkeypatch, case):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(repo / "tests/fixtures/sleeves.csv", source / "prices.csv")
    config = yaml.safe_load(
        (repo / "configs/combined.yaml").read_text(encoding="utf-8")
    )
    config["input"]["path"] = "prices.csv"
    config["source"] = {"evidence_kind": "synthetic"}
    if case == "position":
        config.pop("style")
    path = source / "research.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    before = fingerprints(source)
    checked = preflight(
        "timing-counterfactual", snapshot=path, runs_root=tmp_path / "checks"
    )
    assert checked.status == "checked", checked.as_json()
    assert not (checked.run_dir / "strategy-output").exists()
    checked_evidence = json.loads(
        (checked.run_dir / "preflight.json").read_text(encoding="utf-8")
    )

    if case == "failure":
        # Inject here only; the projection still invokes native verification.
        import quant_timing.counterfactual as native

        original_study, original_process = native.run_study, subprocess.run

        def fail_one(prices, raw, **kwargs):
            if raw.get("run_id", "").endswith("--style_neutral"):
                raise ValueError("injected required candidate failure")
            return original_study(prices, raw, **kwargs)

        def execute(argv, **kwargs):
            if argv[1:4] == ["-m", "quant_timing", "counterfactual"]:
                payload = native.run_counterfactual(
                    path, Path(argv[argv.index("--out") + 1])
                )
                assert payload["status"] == "incomplete"
                return subprocess.CompletedProcess(
                    argv, 2, "injected failure family", ""
                )
            return original_process(argv, **kwargs)

        monkeypatch.setattr(native, "run_study", fail_one)
        monkeypatch.setattr("quant_studio.runner.subprocess.run", execute)
    result = run(
        "timing-counterfactual",
        snapshot=path,
        execute=True,
        runs_root=tmp_path / "runs",
        timeout=180,
    )
    assert result.status == ("failed" if case == "failure" else "succeeded"), (
        result.as_json()
    )
    output = result.run_dir / "strategy-output"
    receipt = json.loads((output / "result.json").read_text(encoding="utf-8"))
    view = json.loads((output / "studio-view/view.json").read_text(encoding="utf-8"))
    assert view["receipt"] == receipt
    assert (
        view["effective_config_sha256"] == checked_evidence["effective_config_sha256"]
    )
    assert view["effective_config"] == checked_evidence["effective_config"]
    assert len(receipt["candidates"]) == (2 if case == "position" else 4)
    assert not list(output.rglob("position_scale.json"))
    assert not list(output.rglob("portfolio_overlay.yaml"))
    assert not (result.run_dir / "nav.csv").exists()
    assert fingerprints(source) == before
    page = render_run(result.run_dir)
    assert "仓位发布：所有候选禁用" in page
    if case == "failure":
        assert (
            "家族结论不可用" in page and "injected required candidate failure" in page
        )
        assert "展开分量差" not in page and not receipt["periods"]
    else:
        assert "研究比较完成" in page and "展开分量差" in page
        assert "描述性全区间" in page
    for name in ("periods.csv", "protocol.json", "result.json", "report.html"):
        download = resolve_run_asset(
            result.run_dir.parent, result.run_id, "strategy-output/" + name
        )
        assert download.read_bytes() == (output / name).read_bytes()
    for relative in (
        "protocol.json",
        "periods.csv",
        "report.html",
        "result.json",
        "runs/base/standard/returns.csv",
        "runs/base/decision.json",
        "studio-view/view.json",
    ):
        target = output / relative
        content = target.read_bytes()
        target.write_bytes(content + b"\n")
        with pytest.raises(QuantStudioError):
            render_run(result.run_dir)
        with pytest.raises(QuantStudioError):
            resolve_run_asset(
                result.run_dir.parent, result.run_id, "strategy-output/periods.csv"
            )
        target.write_bytes(content)
    if case == "style":
        from quant_studio.counterfactual_view import export_view

        for mutation in ("report", "publication", "missing"):
            damaged = tmp_path / mutation
            shutil.copytree(
                output, damaged, ignore=shutil.ignore_patterns("studio-view")
            )
            if mutation == "report":
                (damaged / "report.html").write_text("unverified report")
            elif mutation == "publication":
                (damaged / "runs/base/position_scale.json").write_text("{}")
            else:
                (damaged / "runs/base/attribution/daily.csv").unlink()
            with pytest.raises((ValueError, OSError)):
                export_view(damaged, damaged / "studio-view", "quant-timing")
            assert not (damaged / "studio-view").exists()
