import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.data_maintenance import DataMaintenance
from tests.test_data_catalog import dataset


def test_quality_tracks_declared_coverage_and_detects_changed_bytes(tmp_path):
    item = dataset(
        tmp_path, "symbol,date,value\n00700,2025-01-02,1\n00700,2025-01-02,1\n"
    )
    store = DataMaintenance(tmp_path / "runs")
    report = store.audit(item["id"], "bars.csv", "date", "symbol", ["00700", "00941"])
    assert report["missing_symbols"] == ["00941"]
    assert report["observed_end"] == "2025-01-02"
    assert report["duplicate_keys"] == 1
    (tmp_path / "source/bars.csv").write_text("changed")
    assert not store.report(item["id"])["current"]
    with pytest.raises(QuantStudioError):
        store.audit(item["id"], "bars.csv", "date", "symbol", [])


def test_etf_request_is_bound_to_managed_source(tmp_path):
    store = DataMaintenance(tmp_path)
    source = store.create_etf("指数ETF", ["510300"], "2025-01-02")
    request = store.request(source["id"], "2025-01-03")
    assert request["collection_type"] == "etf-maintenance"
    assert request["expected_snapshot"] is None
    with pytest.raises(QuantStudioError):
        store.request("../outside", "2025-01-03")
    source_path = store.root / "sources" / source["id"] / "source.json"
    original = json.loads(source_path.read_text("utf-8"))
    assert original["symbols"] == ["510300"]
