import base64
import json

from quant_studio.intake_tools import IntakeWorkspace
from quant_studio.intake_web import IntakeHandler, mapping_body, quality_body
from tests.test_server import _request, _start_http_server


def test_upload_requires_csrf_and_mapping_escapes_raw_column_names(
    tmp_path, monkeypatch
):
    def inspect(self, identifier, **kwargs):
        self.source(identifier)
        return {
            "ok": True,
            "data": {
                "columns": [{"name": "<script>alert(1)</script>"}],
                "rows": [["000001"]],
            },
        }

    monkeypatch.setattr(IntakeWorkspace, "inspect", inspect)
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    fields = {
        "filename": "source.csv",
        "content": base64.b64encode(b"a\n000001\n").decode(),
        "encoding": "utf-8-sig",
        "delimiter": ",",
    }
    try:
        status, _, _ = _request(
            server,
            "POST",
            "/intake/upload",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 403
        assert not (tmp_path / ".intake").exists()
        fields["_csrf_token"] = "test-only-csrf-token"
        status, headers, _ = _request(
            server,
            "POST",
            "/intake/upload",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 303
        status, _, body = _request(server, "GET", headers["Location"], host=host)
        assert status == 200
        assert b"<script>alert(1)</script>" not in body
        assert b"000001" in body
        assert "完整检查".encode() in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_parse_failure_keeps_upload_and_offers_safe_retry(tmp_path, monkeypatch):
    def inspect(self, identifier, **kwargs):
        self.source(identifier)
        return {
            "ok": False,
            "error": {"message": "GBK解码失败</p><script>alert(1)</script>"},
        }

    monkeypatch.setattr(IntakeWorkspace, "inspect", inspect)
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    fields = {
        "_csrf_token": "test-only-csrf-token",
        "filename": "旧行情.csv",
        "content": base64.b64encode(b"\x81\n").decode(),
        "encoding": "utf-8-sig",
        "delimiter": ",",
    }
    try:
        status, headers, _ = _request(
            server,
            "POST",
            "/intake/upload",
            host=host,
            origin=f"http://{host}",
            fields=fields,
        )
        assert status == 303
        upload_id = headers["Location"].split("/intake/map/", 1)[1].split("?", 1)[0]
        status, _, body = _request(server, "GET", headers["Location"], host=host)
        assert status == 200
        text = body.decode()
        assert "GBK解码失败" in text
        assert "<script>alert(1)</script>" not in text
        assert "调整编码或分隔符后重试" in text
        assert f'action="/intake/map/{upload_id}"' in text
        assert IntakeWorkspace(tmp_path).source(upload_id).read_bytes() == b"\x81\n"

        status, _, listing = _request(server, "GET", "/intake", host=host)
        assert status == 200
        assert "旧行情.csv" in listing.decode()
        assert upload_id in listing.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_quality_report_handles_missing_data_and_shows_issue_locations():
    failed = quality_body(
        {"ok": False, "error": {"message": "全量检查失败<script>"}, "data": None}
    )
    assert "全量检查失败" in failed
    assert "<script>" not in failed

    page = quality_body(
        {
            "allowed": False,
            "scope": {
                "total_rows": 1250,
                "scanned_rows": 1250,
                "columns": ["date", "close"],
                "start": "2024-01-01",
                "end": "2024-12-31",
                "full_scan": True,
            },
            "issues": [
                {
                    "rule": "missing-close",
                    "severity": "error",
                    "columns": ["close"],
                    "rows": [7, 11],
                    "affected_count": 2,
                    "message": "收盘价缺失",
                    "recommendation": "回到来源补齐，不自动填值",
                }
            ],
        }
    )
    for expected in (
        "1250",
        "2024-01-01",
        "2024-12-31",
        "严重度",
        "error",
        "close",
        "7、11",
        "回到来源补齐，不自动填值",
    ):
        assert expected in page


def test_refresh_reuses_exact_mapping_and_reports_schema_change(tmp_path, monkeypatch):
    store = IntakeWorkspace(tmp_path)
    old = store.upload("旧版.csv", base64.b64encode(b"symbol,close\nA,1\n").decode())
    new = store.upload("新版.csv", base64.b64encode(b"symbol,close\nA,2\n").decode())
    changed = store.upload(
        "变更版.csv", base64.b64encode(b"symbol,close,note\nA,2,x\n").decode()
    )

    def inspect(self, identifier, **kwargs):
        columns = ["symbol", "close"]
        if identifier == changed["id"]:
            columns.append("note")
        return {
            "ok": True,
            "data": {
                "columns": [{"name": name} for name in columns],
                "rows": [["A", "1"] + (["x"] if len(columns) == 3 else [])],
            },
        }

    monkeypatch.setattr(IntakeWorkspace, "inspect", inspect)
    mapping_body(store, "token", old["id"])
    contract = {
        "schema_version": "qdk.research-intake-contract/v1",
        "kind": "daily_bars",
        "input": {"format": "csv", "encoding": "utf-8-sig", "delimiter": ","},
        "mapping": {"symbol": "symbol", "close": "close"},
        "types": {"symbol": "string", "close": "number"},
        "formats": {},
        "primary_key": ["symbol"],
        "metadata": {"provider": "旧供应商", "units": {"close": "CNY"}},
        "limits": {"max_bytes": 1, "max_rows": 100},
    }
    request = store.prepare(old["id"], "已确认行情", contract)
    store.update(
        request["id"],
        status="succeeded",
        response={"ok": True, "data": {"version": {"id": "version-001"}}},
    )

    reused = mapping_body(store, "token", new["id"], reuse_request=request["id"])
    assert 'value="已确认行情"' in reused
    assert 'value="version-001"' in reused
    assert 'name="reuse_request"' in reused
    assert 'name="map_1" value="close"' in reused
    assert 'name="type_1"' in reused and 'value="number" selected' in reused
    assert 'name="unit_1" value="CNY"' in reused

    drifted = mapping_body(store, "token", changed["id"], reuse_request=request["id"])
    assert "检测到原始字段变化" in drifted
    assert "新增列：note" in drifted
    assert "自动复用已停止" in drifted
    assert 'name="reuse_request"' not in drifted
    assert 'value="version-001"' in drifted


def test_intake_submit_leaves_linked_state_to_job_manager(tmp_path):
    store = IntakeWorkspace(tmp_path)
    uploaded = store.upload("source.csv", base64.b64encode(b"symbol\nA\n").decode())
    preview = {"columns": [{"name": "symbol"}], "rows": [["A"]]}
    (store.source(uploaded["id"]).parent / "preview.json").write_text(
        json.dumps(preview), encoding="utf-8"
    )

    class Manager:
        def __init__(self):
            self.request_id = None

        def submit(self, *args, **kwargs):
            self.request_id = kwargs["intake"]["request_id"]
            assert (
                IntakeWorkspace(tmp_path).request(self.request_id)["status"]
                == "prepared"
            )
            return {"job_id": "manager-owned"}

    class Handler(IntakeHandler):
        runs_root = tmp_path
        _request_profile = None

        def __init__(self):
            self.manager = Manager()
            self.location = None

        def _jobs(self):
            return self.manager

        def _redirect(self, location):
            self.location = location

    handler = Handler()
    handler._intake_post(
        "/intake/import",
        {
            "upload": [uploaded["id"]],
            "name": ["示例"],
            "parent": [""],
            "encoding": ["utf-8-sig"],
            "delimiter": [","],
            "kind": ["table"],
            "primary_key": ["symbol"],
            "map_0": ["symbol"],
            "type_0": ["string"],
            "format_0": [""],
            "unit_0": [""],
        },
    )
    record = IntakeWorkspace(tmp_path).request(handler.manager.request_id)
    assert record["status"] == "prepared"
    assert "job_id" not in record
    assert handler.location == f"/intake/requests/{record['id']}"
