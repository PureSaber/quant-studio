import csv
import json
import subprocess
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.fund_reconciliation import (
    BATCH_CONFIRMATION_COLUMNS,
    BATCH_RECEIPT_COLUMNS,
    FundReconciliationStore,
)


def _csv(path, columns, rows=()):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _fund_run(root, classification="synthetic"):
    run = root / "run-one"
    output = run / "strategy-output"
    output.mkdir(parents=True)
    (run / "request.json").write_text(
        json.dumps({"template_id": "fund-fof"}), encoding="utf-8"
    )
    (run / "result.json").write_text(
        json.dumps({"status": "succeeded", "argv": []}), encoding="utf-8"
    )
    (output / "manifest.json").write_text(
        json.dumps({"classification": classification}), encoding="utf-8"
    )
    return run


def test_preview_restricts_paths_and_locks_csv_hashes(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    evidence = tmp_path / "evidence"
    runs.mkdir()
    evidence.mkdir()
    _fund_run(runs)
    confirmations = evidence / "confirm.csv"
    receipts = evidence / "receipts.csv"
    _csv(confirmations, BATCH_CONFIRMATION_COLUMNS)
    _csv(receipts, BATCH_RECEIPT_COLUMNS)
    monkeypatch.setenv("QUANT_FUND_RECONCILE_ROOT", str(evidence))

    store = FundReconciliationStore(runs)
    preview = store.preview("run-one", "batches", confirmations, receipts)

    assert preview["status"] == "previewed"
    assert preview["confirmation_sha256"] and preview["receipt_sha256"]
    assert "reconcile-batches" in preview["argv"]
    outside = tmp_path / "outside.csv"
    _csv(outside, BATCH_CONFIRMATION_COLUMNS)
    with pytest.raises(QuantStudioError, match="允许目录"):
        store.preview("run-one", "batches", outside, receipts)


def test_duplicate_headers_and_changed_input_are_rejected_before_cli(
    tmp_path, monkeypatch
):
    runs = tmp_path / "runs"
    evidence = tmp_path / "evidence"
    runs.mkdir()
    evidence.mkdir()
    _fund_run(runs)
    confirmations = evidence / "confirm.csv"
    receipts = evidence / "receipts.csv"
    confirmations.write_text("evidence_id,evidence_id\n1,1\n", encoding="utf-8")
    _csv(receipts, BATCH_RECEIPT_COLUMNS)
    monkeypatch.setenv("QUANT_FUND_RECONCILE_ROOT", str(evidence))
    store = FundReconciliationStore(runs)
    with pytest.raises(QuantStudioError, match="表头"):
        store.preview("run-one", "batches", confirmations, receipts)

    _csv(confirmations, BATCH_CONFIRMATION_COLUMNS)
    preview = store.preview("run-one", "batches", confirmations, receipts)
    confirmations.write_text(confirmations.read_text() + "changed", encoding="utf-8")
    with pytest.raises(QuantStudioError, match="已改变"):
        store.execute(preview["reconciliation_id"])


def test_cli_difference_is_a_report_and_never_upgrades_classification(
    tmp_path, monkeypatch
):
    runs = tmp_path / "runs"
    evidence = tmp_path / "evidence"
    workspace = tmp_path / "workspace"
    runs.mkdir()
    evidence.mkdir()
    (workspace / "quant-fund").mkdir(parents=True)
    _fund_run(runs, "synthetic")
    confirmations = evidence / "confirm.csv"
    receipts = evidence / "receipts.csv"
    _csv(confirmations, BATCH_CONFIRMATION_COLUMNS)
    _csv(receipts, BATCH_RECEIPT_COLUMNS)
    monkeypatch.setenv("QUANT_FUND_RECONCILE_ROOT", str(evidence))
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(
        "quant_studio.fund_reconciliation.configured_python", lambda *_: sys.executable
    )

    payload = {
        "schema": "quant-fund.batch-reconciliation/v1",
        "status": "differences",
        "read_only": True,
        "real_business_certified": False,
        "classification": "synthetic",
        "as_of": "2026-10-01",
        "confirmation_batches": 1,
        "cash_receipts": 1,
        "orders_awaiting_final_confirmation": [],
        "cash_by_confirmation": [
            {
                "confirmation_id": "c1",
                "confirmed_net": "100",
                "received": "90",
                "outstanding": "10",
                "status": "unpaid",
            }
        ],
        "differences": [{"kind": "cash", "issue": "unpaid"}],
    }

    def invoke(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 2, json.dumps(payload), "")

    monkeypatch.setattr("quant_studio.fund_reconciliation.subprocess.run", invoke)
    store = FundReconciliationStore(runs)
    preview = store.preview("run-one", "batches", confirmations, receipts)
    result = store.execute(preview["reconciliation_id"])

    assert result["status"] == "differences"
    assert result["report"]["cash_by_confirmation"][0]["outstanding"] == "10"
    assert result["report"]["real_business_certified"] is False
    assert result["report"]["classification"] == "synthetic"


def test_cli_cannot_claim_certification_or_change_original_classification(
    tmp_path, monkeypatch
):
    runs = tmp_path / "runs"
    evidence = tmp_path / "evidence"
    workspace = tmp_path / "workspace"
    runs.mkdir()
    evidence.mkdir()
    (workspace / "quant-fund").mkdir(parents=True)
    _fund_run(runs, "synthetic")
    confirmations = evidence / "confirm.csv"
    receipts = evidence / "receipts.csv"
    _csv(confirmations, BATCH_CONFIRMATION_COLUMNS)
    _csv(receipts, BATCH_RECEIPT_COLUMNS)
    monkeypatch.setenv("QUANT_FUND_RECONCILE_ROOT", str(evidence))
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(
        "quant_studio.fund_reconciliation.configured_python", lambda *_: sys.executable
    )
    forged = {
        "schema": "quant-fund.batch-reconciliation/v1",
        "status": "matched",
        "read_only": True,
        "real_business_certified": True,
        "classification": "user_provided",
    }
    monkeypatch.setattr(
        "quant_studio.fund_reconciliation.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, json.dumps(forged), ""
        ),
    )
    store = FundReconciliationStore(runs)
    preview = store.preview("run-one", "batches", confirmations, receipts)

    result = store.execute(preview["reconciliation_id"])

    assert result["status"] == "failed"
    assert "真实性边界" in result["message"] or "数据性质" in result["message"]


def test_csv_change_during_native_execution_is_rejected(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    evidence = tmp_path / "evidence"
    workspace = tmp_path / "workspace"
    runs.mkdir()
    evidence.mkdir()
    (workspace / "quant-fund").mkdir(parents=True)
    _fund_run(runs, "synthetic")
    confirmations = evidence / "confirm.csv"
    receipts = evidence / "receipts.csv"
    _csv(confirmations, BATCH_CONFIRMATION_COLUMNS)
    _csv(receipts, BATCH_RECEIPT_COLUMNS)
    monkeypatch.setenv("QUANT_FUND_RECONCILE_ROOT", str(evidence))
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(
        "quant_studio.fund_reconciliation.configured_python", lambda *_: sys.executable
    )
    payload = {
        "schema": "quant-fund.batch-reconciliation/v1",
        "status": "matched",
        "read_only": True,
        "real_business_certified": False,
        "classification": "synthetic",
        "differences": [],
        "cash_by_confirmation": [],
    }

    def invoke(argv, **kwargs):
        confirmations.write_text(
            confirmations.read_text() + "changed", encoding="utf-8"
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("quant_studio.fund_reconciliation.subprocess.run", invoke)
    store = FundReconciliationStore(runs)
    preview = store.preview("run-one", "batches", confirmations, receipts)

    result = store.execute(preview["reconciliation_id"])

    assert result["status"] == "failed"
    assert "期间CSV输入发生改变" in result["message"]
    assert "report" not in result
