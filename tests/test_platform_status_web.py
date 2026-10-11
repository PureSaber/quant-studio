import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from quant_studio import QuantStudioError
from quant_studio.access import AccessPolicy, password_record
from quant_studio.environment_delivery import DeliveryStore
from quant_studio.platform_status_web import (
    capture_process_identity,
    latest_platform_observation,
    platform_status_body,
    process_observation,
    source_identity,
)
from tests.test_server import _request, _start_http_server
from tests.test_workbench_next import delivery_setup


def test_startup_identity_is_preserved_and_disk_changes_are_visible(
    tmp_path, monkeypatch
):
    package = tmp_path / "repo/src/quant_studio"
    package.mkdir(parents=True)
    source = package / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    root = package.parents[1]
    (root / "requirements.lock").write_text("package==1\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("add", "."),
        (
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    identity = source_identity(package)
    assert len(identity["revision"]) == 40
    assert identity["dirty"] is False
    monkeypatch.setattr(
        "quant_studio.platform_status_web.source_identity",
        lambda: source_identity(package),
    )
    startup = capture_process_identity()
    assert startup["pid"] == os.getpid()
    assert process_observation(startup)["disk_matches_startup"] is True
    source.write_text("value = 2\n", encoding="utf-8")
    observation = process_observation(startup)
    assert observation["disk_matches_startup"] is False
    assert observation["startup_source"] == identity
    assert observation["current_disk_source"]["dirty"] is True
    observation["startup_source"]["revision"] = "changed response copy"
    assert startup["startup_source"] == identity
    assert process_observation(None)["status"] == "not_observed"
    startup["pid"] = -1
    assert process_observation(startup)["status"] == "process_mismatch"


def test_missing_source_and_distribution_do_not_claim_running_code(
    tmp_path, monkeypatch
):
    package = tmp_path / "src/package"
    package.mkdir(parents=True)
    with pytest.raises(QuantStudioError):
        source_identity(package)

    def missing():
        raise OSError("unavailable")

    monkeypatch.setattr("quant_studio.platform_status_web.running_version", missing)
    monkeypatch.setattr("quant_studio.platform_status_web.source_identity", missing)
    startup = capture_process_identity()
    assert startup["installation"]["version"] is None
    assert process_observation(startup)["disk_matches_startup"] is None


def test_delivery_status_uses_fixed_arguments_and_checks_selected_candidate(
    tmp_path, monkeypatch
):
    delivery_setup(tmp_path, monkeypatch)
    store = DeliveryStore(tmp_path / "runs")
    record = store.prepare("platform-status")
    argv = record["request"]["argv"]
    assert (
        "platform-status" in argv and "--source-root" in argv and "--state-dir" in argv
    )
    assert latest_platform_observation(tmp_path / "runs")["status"] == "prepared"
    with pytest.raises(QuantStudioError, match="候选"):
        store.prepare("platform-status", "unknown")
    monkeypatch.setattr(
        "quant_studio.runner._execute_subprocess",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout=json.dumps(
                {
                    "schema_version": "quant.platform-status/v1",
                    "status": "attention_required",
                    "observed_at": "2026-10-11T00:00:00Z",
                }
            ),
        ),
    )
    store.execute(record["id"], None)
    snapshot = latest_platform_observation(tmp_path / "runs")
    assert snapshot["observation"]["status"] == "attention_required"
    assert snapshot["status"] == "recorded_snapshot"
    path = store.directory(record["id"]) / "result.json"
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(QuantStudioError, match="损坏"):
        latest_platform_observation(tmp_path / "runs")
    # Newer pending request is visible, rather than reusing the old result.
    newer = store.prepare("platform-status")
    assert latest_platform_observation(tmp_path / "runs")["request_id"] == newer["id"]


def test_page_escapes_untrusted_report_values(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quant_studio.platform_status_web.process_observation",
        lambda _: {"value": "<script>alert(1)</script>"},
    )
    page = platform_status_body(tmp_path, None)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "not_checked" in page


def test_actual_http_page_uses_current_handler_identity_and_access_gate(tmp_path):
    server, thread = _start_http_server(tmp_path)
    server.RequestHandlerClass.process_identity = capture_process_identity()
    host = f"127.0.0.1:{server.server_port}"
    try:
        status, _, payload = _request(server, "GET", "/platform-status", host=host)
        assert status == 200
        assert str(os.getpid()).encode() in payload
        assert b"same_process" in payload
        status, _, _ = _request(
            server, "GET", "/platform-status", host="foreign.invalid"
        )
        assert status == 403
        access = tmp_path / "access.json"
        access.write_text(
            json.dumps(password_record("test-platform-long-password", ["127.0.0.1"])),
            encoding="utf-8",
        )
        server.RequestHandlerClass.access_policy = AccessPolicy(access)
        status, headers, _ = _request(server, "GET", "/platform-status", host=host)
        assert status == 303
        assert headers["Location"].startswith("/login")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
