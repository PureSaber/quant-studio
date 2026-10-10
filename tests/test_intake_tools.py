import base64
import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.intake_tools import IntakeWorkspace, contract_from_form


def test_upload_preserves_source_bytes_and_detects_later_changes(tmp_path):
    raw = "代码,日期,收盘\n000001,2026-10-09,12.5\n".encode()
    store = IntakeWorkspace(tmp_path)
    item = store.upload("行情.csv", base64.b64encode(raw).decode())
    assert store.source(item["id"]).read_bytes() == raw
    assert item["filename"] == "行情.csv"
    store.source(item["id"]).write_bytes(raw + b"changed")
    with pytest.raises(QuantStudioError, match="变化"):
        store.source(item["id"])


@pytest.mark.parametrize(
    "filename", ["../a.csv", "C:\\a.csv", "script.py", "a.csv:stream"]
)
def test_upload_rejects_paths_and_unsupported_files(tmp_path, filename):
    with pytest.raises(QuantStudioError):
        IntakeWorkspace(tmp_path).upload(filename, base64.b64encode(b"a\n1\n").decode())


def test_contract_maps_explicit_types_units_and_date_format():
    form = {
        "kind": ["daily_bars"],
        "source": ["手动导出"],
        "provider": ["已声明来源"],
        "encoding": ["utf-8-sig"],
        "delimiter": [","],
        "timezone": ["Asia/Shanghai"],
        "adjustment": ["raw"],
        "primary_key": ["symbol,date"],
        "map_0": ["symbol"],
        "type_0": ["string"],
        "format_0": [""],
        "unit_0": [""],
        "map_1": ["date"],
        "type_1": ["date"],
        "format_1": ["%Y-%m-%d"],
        "unit_1": [""],
        "map_2": ["close"],
        "type_2": ["number"],
        "format_2": [""],
        "unit_2": ["CNY"],
    }
    contract = contract_from_form(form, ["代码", "日期", "收盘"])
    assert contract["mapping"] == {"symbol": "代码", "date": "日期", "close": "收盘"}
    assert contract["types"]["symbol"] == "string"
    assert contract["metadata"]["units"]["close"] == "CNY"
    assert contract["formats"]["date"] == "%Y-%m-%d"
    assert not form


def test_duplicate_mapping_is_rejected():
    form = {"kind": ["table"], "source": ["source"], "map_0": ["a"], "map_1": ["a"]}
    with pytest.raises(QuantStudioError, match="重复"):
        contract_from_form(form, ["x", "y"])


def test_prepared_request_pins_upload_and_contract(tmp_path):
    store = IntakeWorkspace(tmp_path)
    item = store.upload("a.csv", base64.b64encode(b"symbol\n000001\n").decode())
    contract = {"schema_version": "qdk.research-intake-contract/v1", "kind": "table"}
    request = store.prepare(item["id"], "example", contract)
    record = store.request(request["id"])
    assert record["status"] == "prepared"
    assert record["source_sha256"] == item["sha256"]
    path = store.root / "requests" / request["id"] / "contract.json"
    path.write_text(json.dumps({"changed": True}))
    with pytest.raises(QuantStudioError, match="契约"):
        store.request(request["id"])
