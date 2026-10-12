import base64
import json
from types import SimpleNamespace

import pytest

from quant_studio import QuantStudioError
from quant_studio.intake_tools import IntakeWorkspace
from quant_studio.intake_web import (
    _refresh_context,
    _refresh_options,
    intake_body,
    mapping_body,
)


@pytest.mark.parametrize(
    "version_status,report_status,expected",
    [
        ("ready", "ready", "succeeded"),
        ("blocked", "blocked", "blocked"),
        ("ready", "blocked", "blocked"),
        (None, None, "blocked"),
    ],
)
def test_completed_import_does_not_imply_quality_approval(
    tmp_path, monkeypatch, version_status, report_status, expected
):
    store = IntakeWorkspace(tmp_path)
    item = store.upload("input.csv", base64.b64encode(b"symbol\n000001\n").decode())
    record = store.prepare(item["id"], "quality-check", {})
    response = {
        "ok": True,
        "data": {
            "receipt": {"status": "succeeded"},
            "version": {"id": "version-001", "status": version_status},
            "report": {"status": report_status},
        },
    }
    monkeypatch.setattr(store, "backend", lambda *a, **k: response)
    result = store.execute(record["id"], SimpleNamespace(cancel_requested=False))
    assert result["status"] == expected
    saved = store.request(record["id"])
    assert saved["status"] == expected
    assert saved["response"] == response


def test_historical_false_success_cannot_be_reused_or_shown_as_published(
    tmp_path, monkeypatch
):
    store = IntakeWorkspace(tmp_path)
    item = store.upload("input.csv", base64.b64encode(b"symbol\n000001\n").decode())
    record = store.prepare(item["id"], "blocked-history", {})
    store.update(
        record["id"],
        status="succeeded",
        response={
            "ok": True,
            "data": {
                "version": {"id": "blocked-version", "status": "blocked"},
                "report": {"status": "blocked"},
            },
        },
    )
    path = store.root / "requests" / record["id"] / "request.json"
    original = path.read_bytes()
    monkeypatch.setattr(
        store, "backend", lambda *a, **k: {"ok": True, "data": {"datasets": []}}
    )
    page = intake_body(store, "test-token")
    assert "质量检查未通过" in page
    assert "已通过并发布" not in page
    assert "可在字段页选择复用此映射" not in page
    assert _refresh_options(store) == []
    with pytest.raises(QuantStudioError, match="只能复用已发布"):
        _refresh_context(store, record["id"], ["symbol"])
    assert path.read_bytes() == original


def test_only_datasets_with_a_published_latest_version_are_linked(
    tmp_path, monkeypatch
):
    store = IntakeWorkspace(tmp_path)
    (store.root / "catalog").mkdir(parents=True)
    monkeypatch.setattr(
        store,
        "backend",
        lambda *a, **k: {
            "ok": True,
            "data": {
                "datasets": [
                    {"name": "blocked-only", "latest": None, "version_count": 1},
                    {"name": "ready-data", "latest": "ready-001", "version_count": 2},
                ]
            },
        },
    )
    page = intake_body(store, "test-token")
    assert (
        "name=ready-data&amp;version=ready-001" in page
        or "name=ready-data&version=ready-001" in page
    )
    assert "name=blocked-only" not in page


@pytest.mark.parametrize("name", ["中文名称", "bad name", "CON", "aux.csv", "data."])
def test_invalid_dataset_identifier_is_rejected_before_request_creation(tmp_path, name):
    store = IntakeWorkspace(tmp_path)
    item = store.upload("input.csv", base64.b64encode(b"symbol\n000001\n").decode())
    with pytest.raises(QuantStudioError, match="数据集标识"):
        store.prepare(item["id"], name, {})
    assert not (store.root / "requests").exists()
    assert store.source(item["id"]).read_bytes() == b"symbol\n000001\n"


def test_initial_catalog_is_empty_without_creating_it_or_calling_backend(
    tmp_path, monkeypatch
):
    store = IntakeWorkspace(tmp_path)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("An uninitialized catalog is not an environment error")

    monkeypatch.setattr(store, "backend", unexpected_call)
    page = intake_body(store, "test-token")
    assert "尚未发布数据版本" in page
    assert not store.root.exists()


def test_missing_catalog_after_success_still_reports_integrity_failure(
    tmp_path, monkeypatch
):
    store = IntakeWorkspace(tmp_path)
    item = store.upload("input.csv", base64.b64encode(b"symbol\n000001\n").decode())
    record = store.prepare(item["id"], "valid_name", {})
    record["status"] = "succeeded"
    (store.root / "requests" / record["id"] / "request.json").write_text(
        json.dumps(record)
    )
    monkeypatch.setattr(
        store,
        "backend",
        lambda *a, **k: {"ok": False, "error": {"message": "catalog missing"}},
    )
    page = intake_body(store, "test-token")
    assert "catalog missing" in page
    assert "尚未发布数据版本" not in page


def test_non_ascii_filename_has_valid_suggested_identifier_and_guidance(
    tmp_path, monkeypatch
):
    store = IntakeWorkspace(tmp_path)
    item = store.upload("我的行情.csv", base64.b64encode(b"symbol\n000001\n").decode())
    monkeypatch.setattr(
        store,
        "inspect",
        lambda *a, **k: {
            "ok": True,
            "data": {"columns": [{"name": "symbol"}], "rows": [["000001"]]},
        },
    )
    page = mapping_body(store, "test-token", item["id"])
    assert "数据集标识" in page
    assert "英文字母" in page
    assert f'value="dataset-{item["id"][:12]}"' in page
    assert 'maxlength="120"' in page
    assert "我的行情.csv" in page
