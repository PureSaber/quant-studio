import csv
import json
import subprocess
import sys
import threading
import time

from quant_studio.fund_reconciliation import (
    BATCH_CONFIRMATION_COLUMNS,
    BATCH_RECEIPT_COLUMNS,
)
from quant_studio.runner import run
from tests.test_server import _request, _start_http_server


def _wait(manager, job_id, states, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] in states:
            return job
        time.sleep(0.01)
    raise AssertionError(manager.get(job_id))


def _csv(path, columns):
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.DictWriter(stream, fieldnames=columns).writeheader()


def test_http_queue_detail_and_queued_cancel_keep_csrf_boundary(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = run

    def slow_run(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr("quant_studio.server.run", slow_run)
    server, thread = _start_http_server(tmp_path)
    authority = f"localhost:{server.server_port}"
    origin = f"http://{authority}"
    fields = {"_csrf_token": "test-only-csrf-token", "action": "execute"}
    try:
        first_status, first_headers, _ = _request(
            server,
            "POST",
            "/templates/synthetic-demo",
            host=authority,
            origin=origin,
            fields=fields,
        )
        assert first_status == 303 and entered.wait(5)
        second_status, second_headers, _ = _request(
            server,
            "POST",
            "/templates/synthetic-demo",
            host=authority,
            origin=origin,
            fields=fields,
        )
        assert second_status == 303
        job_id = second_headers["Location"].removeprefix("/jobs/")
        manager = server.RequestHandlerClass.job_manager
        assert manager.get(job_id)["status"] == "queued"
        denied, _, _ = _request(
            server,
            "POST",
            f"/jobs/{job_id}/cancel",
            host=authority,
            origin=origin,
            fields={"_csrf_token": "wrong"},
        )
        assert denied == 403 and manager.get(job_id)["status"] == "queued"
        cancelled, headers, _ = _request(
            server,
            "POST",
            f"/jobs/{job_id}/cancel",
            host=authority,
            origin=origin,
            fields={"_csrf_token": "test-only-csrf-token"},
        )
        assert cancelled == 303 and headers["Location"] == f"/jobs/{job_id}"
        assert manager.get(job_id)["status"] == "cancelled"
        status, _, page = _request(server, "GET", f"/jobs/{job_id}", host=authority)
        assert status == 200 and "已取消".encode() in page
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_fund_reconciliation_requires_preview_then_explicit_execute(
    tmp_path, monkeypatch
):
    runs = tmp_path / "runs"
    evidence = tmp_path / "evidence"
    workspace = tmp_path / "workspace"
    output = runs / "fund-run" / "strategy-output"
    output.mkdir(parents=True)
    evidence.mkdir()
    (workspace / "quant-fund").mkdir(parents=True)
    (output.parent / "request.json").write_text(
        json.dumps({"template_id": "fund-fof"}), encoding="utf-8"
    )
    (output.parent / "result.json").write_text(
        json.dumps({"status": "succeeded", "argv": []}), encoding="utf-8"
    )
    (output / "manifest.json").write_text(
        json.dumps({"classification": "synthetic"}), encoding="utf-8"
    )
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
        "differences": [{"kind": "cash", "issue": "unpaid"}],
        "cash_by_confirmation": [],
    }
    calls = []

    def invoke(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 2, json.dumps(payload), "")

    monkeypatch.setattr("quant_studio.fund_reconciliation.subprocess.run", invoke)
    server, thread = _start_http_server(runs)
    authority = f"localhost:{server.server_port}"
    origin = f"http://{authority}"
    try:
        status, headers, _ = _request(
            server,
            "POST",
            "/fund-reconcile",
            host=authority,
            origin=origin,
            fields={
                "_csrf_token": "test-only-csrf-token",
                "run_id": "fund-run",
                "mode": "batches",
                "confirmations": str(confirmations),
                "receipts": str(receipts),
            },
        )
        assert status == 303 and headers["Location"].startswith("/reconciliations/")
        assert calls == []
        identifier = headers["Location"].removeprefix("/reconciliations/")
        preview_status, _, preview = _request(
            server, "GET", headers["Location"], host=authority
        )
        assert preview_status == 200 and "显式执行".encode() in preview
        status, _, _ = _request(
            server,
            "POST",
            f"/reconciliations/{identifier}/execute",
            host=authority,
            origin=origin,
            fields={"_csrf_token": "test-only-csrf-token"},
        )
        assert status == 303 and len(calls) == 1
        status, _, page = _request(
            server, "GET", f"/reconciliations/{identifier}", host=authority
        )
        assert status == 200
        assert b"real_business_certified=False" in page
        assert "差异".encode() in page
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
