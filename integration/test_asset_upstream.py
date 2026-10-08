"""Exercise native applications through independently configured Python runtimes."""

import csv
import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from quant_studio.nav import parse_nav_csv
from quant_studio.runner import preflight, run
from quant_studio.runtime import configured_python
from quant_studio.server import render_run
from quant_studio.templates import load_template


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


@pytest.mark.parametrize("name", ["fund-fof", "us-equity-research"])
def test_native_asset_cli(name, tmp_path, monkeypatch):
    template = load_template(name)
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    assert repo.name == template.workspace_repo
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    python = configured_python(template.workspace_repo) or sys.executable
    source = tmp_path / "synthetic inputs"
    fund = name == "fund-fof"
    demo = (
        "from quant_fund.demo import create_demo; create_demo(sys.argv[1])"
        if fund
        else (
            "from quant_us_equity.demo import demo_bundle; "
            "demo_bundle(sys.argv[1], 271)"
        )
    )
    created = subprocess.run(
        [python, "-c", "import sys; " + demo, str(source)],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    settings = (
        {"start": "2024-01-02", "end": "2024-04-30", "initial_cash": 250000}
        if fund
        else {"benchmark": "US:DEMO:SPY", "initial_cash": 250000}
    )
    before = fingerprint(source)
    checked = preflight(
        template, settings, snapshot=source, runs_root=tmp_path / "checks"
    )
    assert checked.status == "checked", diagnostics(checked)
    assert checked.evidence_kind == "synthetic"
    assert not (checked.run_dir / "strategy-output").exists()
    assert fingerprint(source) == before
    evidence = json.loads((checked.run_dir / "preflight.json").read_text())
    assert evidence["read_only"] is True and evidence["investable"] is False
    assert "合成数据" in render_run(checked.run_dir)

    result = run(
        template,
        settings,
        snapshot=source,
        execute=True,
        runs_root=tmp_path / "runs",
        timeout=240,
    )
    assert result.status == "succeeded", diagnostics(result)
    assert result.evidence_kind == "synthetic"
    output = result.run_dir / "strategy-output"
    if fund:
        assert result.verification_status == "pass"
        evidence = json.loads(
            (result.run_dir / "verification.json").read_text(encoding="utf-8")
        )
        assert evidence["verified_ledger_days"] > 60
        assert "账本重放校验通过" in render_run(result.run_dir)
        _exercise_fund_reconciliation(python, repo, output, tmp_path)
    assert fingerprint(source) == before
    assert (checked.run_dir / "config.json").read_bytes() == (
        result.run_dir / "config.json"
    ).read_bytes()
    nav = parse_nav_csv(result.run_dir / "nav.csv")
    assert len(nav.rows) > 60
    assert nav.initial_nav == (1 if fund else 250000)
    native = json.loads(
        (output / ("metrics.json" if fund else "study.json")).read_text(
            encoding="utf-8"
        )
    )
    metrics = native if fund else native["metrics"]
    total = metrics["full_period_return" if fund else "total_return"]
    drawdown = metrics["full_period_max_drawdown" if fund else "max_drawdown"]
    assert float(nav.period_return) == pytest.approx(total, abs=1e-12)
    assert float(nav.max_drawdown) == pytest.approx(-drawdown, abs=1e-12)
    if not fund:
        baseline = parse_nav_csv(result.run_dir / "benchmark_nav.csv")
        assert baseline.initial_nav == 250000
        assert float(baseline.period_return) == pytest.approx(
            native["benchmark_metrics"]["total_return"], abs=1e-12
        )
        assert "同区间基准：" in render_run(result.run_dir)
    assert "合成数据" in render_run(result.run_dir)
    assert "<iframe" in render_run(result.run_dir)
    assert result.report == "strategy-output/report.html"
    verification = (
        ["quant_fund.cli", "verify-run", str(output)]
        if fund
        else ["quant_us_equity.cli", "verify", "--run", str(output)]
    )
    verified = subprocess.run(
        [python, "-m", *verification],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr

    # Corrupt only a freshly generated disposable input, keeping its marker present.
    bad = source / ("nav.csv" if fund else "prices.csv")
    bad.write_text("invalid,input\n", encoding="utf-8")
    broken = fingerprint(source)
    rejected = preflight(
        template, settings, snapshot=source, runs_root=tmp_path / "checks"
    )
    assert rejected.status == "check_failed", diagnostics(rejected)
    assert not (rejected.run_dir / "strategy-output").exists()
    failed = run(
        template, settings, snapshot=source, execute=True, runs_root=tmp_path / "runs"
    )
    assert failed.status == "failed", diagnostics(failed)
    assert not (failed.run_dir / "nav.csv").exists()
    assert fingerprint(source) == broken


def _exercise_fund_reconciliation(python, repo, output, tmp_path):
    from quant_fund.batches import (
        BATCH_CONFIRMATION_COLUMNS,
        BATCH_RECEIPT_COLUMNS,
    )
    from quant_fund.observations import CONFIRMATION_COLUMNS, RECEIPT_COLUMNS

    with (output / "trades.csv").open(encoding="utf-8") as stream:
        trades = list(csv.DictReader(stream))
    with (output / "nav.csv").open(encoding="utf-8") as stream:
        cutoff = list(csv.DictReader(stream))[-1]["date"]
    confirmations = [
        {
            **{key: trade[key] for key in CONFIRMATION_COLUMNS if key in trade},
            "evidence_id": f"synthetic-confirm-{trade['order_id']}",
            "currency": "CNY",
            "source_ref": "synthetic:studio-integration",
        }
        for trade in trades
    ]
    receipts = []
    for trade in trades:
        if trade["side"] != "SELL" or trade["settle_date"] > cutoff:
            continue
        receipts.append(
            {
                "evidence_id": f"synthetic-receipt-{trade['order_id']}",
                "order_id": trade["order_id"],
                "fund_id": trade["fund_id"],
                "received": trade["settle_date"],
                "amount": str(Decimal(trade["gross"]) - Decimal(trade["fee"])),
                "currency": "CNY",
                "source_ref": "synthetic:studio-integration",
            }
        )
    evidence = tmp_path / "reconciliation"
    evidence.mkdir()

    def write(name, columns, rows):
        path = evidence / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return path

    simple_confirmations = write(
        "confirmations.csv", CONFIRMATION_COLUMNS, confirmations
    )
    simple_receipts = write("receipts.csv", RECEIPT_COLUMNS, receipts)
    before = fingerprint(output)

    def reconcile(command, confirmation_path, receipt_path):
        return subprocess.run(
            [
                python,
                "-m",
                "quant_fund.cli",
                command,
                "--run",
                str(output),
                "--confirmations",
                str(confirmation_path),
                "--receipts",
                str(receipt_path),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )

    checked = reconcile("reconcile-observations", simple_confirmations, simple_receipts)
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["status"] == "matched"
    assert report["read_only"] is True
    assert report["real_business_certified"] is False
    assert report["classification"] == "synthetic"

    batch_confirmations = [{**row, "final": "true"} for row in confirmations]
    parents = {row["order_id"]: row["evidence_id"] for row in confirmations}
    batch_receipts = [
        {**row, "confirmation_id": parents[row["order_id"]]} for row in receipts
    ]
    batch_confirmation_path = write(
        "batch-confirmations.csv", BATCH_CONFIRMATION_COLUMNS, batch_confirmations
    )
    batch_receipt_path = write(
        "batch-receipts.csv", BATCH_RECEIPT_COLUMNS, batch_receipts
    )
    batched = reconcile(
        "reconcile-batches", batch_confirmation_path, batch_receipt_path
    )
    assert batched.returncode == 0, batched.stderr
    batch_report = json.loads(batched.stdout)
    assert batch_report["status"] == "matched"
    assert batch_report["real_business_certified"] is False
    assert all(row["status"] == "paid" for row in batch_report["cash_by_confirmation"])
    assert fingerprint(output) == before

    duplicate_path = write(
        "duplicate-batches.csv",
        BATCH_CONFIRMATION_COLUMNS,
        [*batch_confirmations, batch_confirmations[0]],
    )
    rejected = reconcile("reconcile-batches", duplicate_path, batch_receipt_path)
    assert rejected.returncode != 0
    assert fingerprint(output) == before
