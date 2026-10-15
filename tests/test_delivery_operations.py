import json
import os
from pathlib import Path

import pytest

from quant_studio import QuantStudioError
from quant_studio.environment_delivery import (
    ACTIONS,
    DeliveryStore,
    delivery_config,
    operational_snapshot,
)
from quant_studio.maintenance_web import delivery_body
from quant_studio.operations import operations_body
from quant_studio.settings import use_settings
from tests.test_workbench_next import delivery_setup


def configured(tmp_path, monkeypatch):
    delivery_setup(tmp_path, monkeypatch)
    path = tmp_path / "delivery.json"
    config = json.loads(path.read_text())
    control = tmp_path / "control.json"
    backup = tmp_path / "backup.json"
    state = tmp_path / "controller-state"
    state.mkdir()
    (state / "process.json").write_text('{"pid":123,"verified_at":"old"}')
    (state / "last-operation.json").write_text('{"status":"failed","error":"<unsafe>"}')
    (state / "backup.json").write_text('{"verified":true,"restore_verified":false}')
    (state / "restore.json").write_text('{"status":"verified","restart_ready":false}')
    control.write_text(json.dumps({"state_dir": str(state)}))
    archive = tmp_path / "backup.zip"
    archive.write_bytes(b"fixture")
    backup.write_text(json.dumps({"state_dir": str(state), "archive": str(archive)}))
    config.update(
        local_control=str(control), backups={"daily": {"config": str(backup)}}
    )
    path.write_text(json.dumps(config))
    return path, config


def test_delivery_uses_server_settings_and_preserves_environment_fallback(
    tmp_path, monkeypatch
):
    path, config = configured(tmp_path, monkeypatch)
    monkeypatch.delenv("QUANT_STUDIO_DELIVERY_CONFIG")
    with use_settings(
        {
            "environment": {"QUANT_STUDIO_DELIVERY_CONFIG": str(path)},
            "python_by_repo": {},
        }
    ):
        assert delivery_config()[0] == config
        assert (
            DeliveryStore(tmp_path).prepare("control-status")["request"]["argv"][-1]
            == "status"
        )


def test_queue_only_verifies_or_restores_and_checks_reviewed_files(
    tmp_path, monkeypatch
):
    _, config = configured(tmp_path, monkeypatch)
    store = DeliveryStore(tmp_path / "runs")
    record = store.prepare("verify-backup", "daily")
    assert record["request"]["argv"][-1] == "verify"
    assert "quant_workspace.operational_backup" in record["request"]["argv"]
    assert (
        store.prepare("restore-drill", "daily")["request"]["argv"][-1]
        == "restore-drill"
    )
    assert "backup" not in ACTIONS and "restart" not in ACTIONS
    with pytest.raises(QuantStudioError, match="请选择"):
        store.prepare("verify-backup", "../unlisted")
    Path(config["backups"]["daily"]["config"]).write_text('{"drift":true}')
    with pytest.raises(QuantStudioError, match="变化"):
        store.execute(record["id"], None)


def test_operational_pages_distinguish_current_process_and_historical_receipts(
    tmp_path, monkeypatch
):
    configured(tmp_path, monkeypatch)
    snapshot = operational_snapshot()
    assert snapshot["studio_process"]["pid"] == os.getpid()
    assert snapshot["local_control"]["process"]["pid"] == 123
    assert snapshot["backups"]["daily"]["restore"]["restart_ready"] is False
    for body in (
        delivery_body(tmp_path / "runs", "token"),
        operations_body(tmp_path / "runs"),
    ):
        assert "冻结" in body and "历史" in body
        assert "&lt;unsafe&gt;" in body and "<unsafe>" not in body
    body = delivery_body(tmp_path / "runs", "token")
    assert "--expected-plan-sha256 $backupPlan.plan_sha256" in body
    assert "Studio关闭" in body


def test_bad_controller_and_frozen_probe_are_honest_not_page_failures(
    tmp_path, monkeypatch
):
    _, config = configured(tmp_path, monkeypatch)
    Path(config["local_control"]).write_text("broken")
    result = operational_snapshot()
    assert "delivery_error" in result
    assert result["studio_process"]["pid"] == os.getpid()


def test_etf_installation_shows_repeatable_entrypoint_and_matching_actions(
    tmp_path, monkeypatch
):
    path, config = configured(tmp_path, monkeypatch)
    config.update(
        installation_kind="etf-single-venv",
        maintenance_entrypoint=str(tmp_path / "profile/maintain.ps1"),
        maintenance_installation=str(tmp_path / "installation.json"),
    )
    path.write_text(json.dumps(config))
    release_state = Path(config["state_dir"])
    release_state.mkdir(parents=True, exist_ok=True)
    (release_state / "etf-current.json").write_text(
        '{"runtime_root":"accepted-etf-runtime"}'
    )
    body = delivery_body(tmp_path / "runs", "token")
    assert "-Action backup" in body and "-Action restart" in body
    assert '<option value="doctor">' not in body
    assert '<option value="control-status">' in body
    assert "自动创建独立批次" in body
    assert "accepted-etf-runtime" in body
    assert "尚无发布指针" not in body
    config["maintenance_entrypoint"] = "relative.ps1"
    path.write_text(json.dumps(config))
    with pytest.raises(QuantStudioError, match="绝对路径"):
        delivery_config()
