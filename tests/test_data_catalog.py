import hashlib
import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.data_catalog import catalog, preview_file
from quant_studio.datasets import DatasetStore


def dataset(root, content):
    source = root / "source"
    source.mkdir()
    (source / "bars.csv").write_text(content, encoding="utf-8")
    digest = hashlib.sha256((source / "bars.csv").read_bytes()).hexdigest()
    (source / "manifest.json").write_text(
        json.dumps({"provider": "synthetic", "files": {"bars.csv": digest}})
    )
    # A-share registration has no required filenames; business preflight is separate.
    return DatasetStore(root / "runs").register(
        "成交量数据", "a-share-four-factor", str(source)
    )


def test_preview_reports_observed_missingness_without_guessing_semantics(tmp_path):
    item = dataset(
        tmp_path, "symbol,date,volume\n00700,2025-01-02,100\n00700,2025-01-03,\n"
    )
    report = preview_file(tmp_path / "runs", item["id"], "bars.csv")
    assert report["rows"][0][0] == "00700"
    assert report["scanned_rows"] == 2
    assert report["columns"][2]["missing"] == 1
    assert report["scan_complete"]
    assert report["columns"][2]["description"] == "未声明"
    assert len(catalog(tmp_path / "runs", query="成交量")) == 1
    assert catalog(tmp_path / "runs", query="不存在") == []


def test_preview_is_bounded_and_rejects_changed_or_unregistered_files(tmp_path):
    item = dataset(tmp_path, "value\n1\n2\n3\n")
    report = preview_file(tmp_path / "runs", item["id"], "bars.csv", max_rows=2)
    assert report["scanned_rows"] == 2 and not report["scan_complete"]
    for filename in ("../outside.csv", "missing.csv"):
        with pytest.raises(QuantStudioError):
            preview_file(tmp_path / "runs", item["id"], filename)
    (tmp_path / "source/bars.csv").write_text("value\n9\n")
    with pytest.raises(QuantStudioError, match="变化|完整"):
        preview_file(tmp_path / "runs", item["id"], "bars.csv")
