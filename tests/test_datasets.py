import hashlib
import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, collection_request


def hk_fixture(root):
    root.mkdir()
    (root / "bars.parquet").write_bytes(b"fixture bars")
    (root / "calendar.csv").write_text("date\n2025-01-02\n")
    manifest = {
        "schema": "quant-hk-snapshot/v1",
        "status": "complete",
        "provider": "fixture",
        "symbols": ["00700"],
        "start": "2025-01-01",
        "end": "2025-02-01",
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest))


def test_registered_dataset_is_version_bound_and_rejects_changed_files(tmp_path):
    data = tmp_path / "hk"
    hk_fixture(data)
    store = DatasetStore(tmp_path / "runs")
    item = store.register("港股样本", "hk-equity-daily", str(data))
    assert item["symbols"] == ["00700"]
    assert item["start"] == "2025-01-01"
    assert store.get(item["id"])["path"] == str(data.resolve())
    assert store.verify(item["id"])["valid"]
    (data / "bars.parquet").write_bytes(b"changed")
    assert not store.verify(item["id"])["valid"]
    with pytest.raises(QuantStudioError, match="变化|完整"):
        store.select(item["id"], "hk-equity-daily")
    with pytest.raises(QuantStudioError):
        store.get("../bad")


def test_collection_requests_bound_provider_symbols_dates_and_runtime(tmp_path):
    value = collection_request(
        "港股",
        "hk-equity-daily",
        "00700, 00005",
        "2025-01-01",
        "2025-06-30",
        "akshare_sina_hk",
    )
    assert value["symbols"] == ["00700", "00005"]
    with pytest.raises(QuantStudioError):
        collection_request(
            "x",
            "hk-equity-daily",
            "00700;bad",
            "2025-01-01",
            "2025-06-30",
            "akshare_sina_hk",
        )
    with pytest.raises(QuantStudioError):
        collection_request(
            "x",
            "hk-equity-daily",
            "00700",
            "2025-06-30",
            "2025-01-01",
            "akshare_sina_hk",
        )
    with pytest.raises(QuantStudioError):
        collection_request(
            "x", "hk-equity-daily", "00700", "2025-01-01", "2025-06-30", "http://evil"
        )
