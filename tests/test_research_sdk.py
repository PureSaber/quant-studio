import json

import pytest

from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from quant_studio.research_sdk import Research
from tests.test_data_catalog import dataset
from tests.test_notebook_research import configure


def test_sdk_is_frozen_and_metrics_do_not_hide_missing(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    item = dataset(tmp_path, "symbol,date,close\n00700,2025-01-02,10\n")
    root = tmp_path / "runs"
    p = ProjectStore(root).save("SDK", "统一读取", datasets=[item["id"]])
    store = NotebookStore(root)
    store.initialize(p["id"])
    run = store.prepare(p["id"], p["revision"])
    assert "research.py" in run["hashes"]
    research = Research(store.directory(p["id"], run["id"]) / "inputs.json")
    assert research.datasets()[0]["id"] == item["id"]
    research.metric(
        "均值", None, unit="比例", definition="有效收益均值", reason="无样本"
    )
    assert research.metrics["均值"]["value"] is None
    with pytest.raises(ValueError):
        research.metric("均值", float("nan"), unit="比例", definition="均值")


def test_alignment_does_not_fill_gaps_or_merge_duplicates(tmp_path):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {
            "date": ["2025-01-01", "2025-01-02", "2025-01-01"],
            "symbol": ["A", "A", "B"],
            "close": [10, 11, 20],
        }
    )
    panel = Research.align(frame, value="close")
    assert pd.isna(panel.iloc[1]["B"])
    assert pd.isna(Research.returns(panel).iloc[1]["B"])
    with pytest.raises(ValueError, match="重复"):
        Research.align(pd.concat([frame, frame.iloc[:1]]), value="close")
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps({"datasets": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        Research(path).load("../outside")
