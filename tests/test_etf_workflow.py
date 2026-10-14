import json

import pytest

from quant_studio import QuantStudioError, etf_project
from quant_studio.datasets import DatasetStore
from quant_studio.etf_daily_web import today_body
from quant_studio.jobs import JobManager
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore


def test_connect_freezes_inputs_and_duplicate_click_reuses_project(
    tmp_path, monkeypatch
):
    from quant_studio.datasets import file_hash

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest = {
        "symbols": ["510300"],
        "source": {"provider": "fixture"},
        "requested_start": "2025-01-01",
        "requested_end": "2026-01-01",
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        json.dumps(
            {
                "hypothesis": "四ETF研究",
                "inputs": {
                    "bundle": str(snapshot),
                    "catalog": str(snapshot / "catalog.csv"),
                },
            }
        ),
        encoding="utf-8",
    )
    operation = {
        "id": "a" * 32,
        "research_recipe": str(recipe_path),
        "research_recipe_sha256": file_hash(recipe_path),
        "data": {"covered_through": "2026-01-01", "quality": {}},
    }
    monkeypatch.setattr(
        etf_project,
        "inspect_daily",
        lambda: {
            "latest": {"id": "failed-refresh", "state": "collection_retryable"},
            "research_input": operation,
        },
    )
    root = tmp_path / "runs"
    root.mkdir()
    project = etf_project.connect(root, operation["id"])
    assert etf_project.connect(root, operation["id"])["id"] == project["id"]
    assert not project.get("research_card")
    ref = project["recipes"][0]
    recipe = RecipeStore(root).get(ref["id"], ref["revision"])
    assert DatasetStore(root).select(recipe["dataset_id"], "etf-weekly-research")
    existing = ProjectStore(root).save("原有研究", "同一经济假设", notes="保留负面结论")
    linked = etf_project.connect(root, operation["id"], project_id=existing["id"])
    assert linked["id"] == existing["id"]
    assert linked["notes"] == "保留负面结论" and len(linked["recipes"]) == 1
    assert (
        etf_project.connect(root, operation["id"], project_id=existing["id"]) == linked
    )
    recipe_path.write_text("modified")
    assert recipe["input_config"]["config"]["hypothesis"] == "四ETF研究"
    with pytest.raises(QuantStudioError, match="变化"):
        etf_project.connect(root, operation["id"])


def test_etf_run_requires_explicit_project_and_card(tmp_path):
    jobs = JobManager(tmp_path, execute=lambda *_: None, start_worker=False)
    try:
        with pytest.raises(QuantStudioError, match="项目"):
            jobs.submit("execute", "etf-weekly-research", {}, profile=None)
        project = ProjectStore(tmp_path).save("研究", "问题")
        with pytest.raises(QuantStudioError, match="研究卡"):
            jobs.submit(
                "execute",
                "etf-weekly-research",
                {},
                profile=None,
                research_project={"id": project["id"], "revision": project["revision"]},
            )
    finally:
        jobs.close()


def test_today_distinguishes_unavailable_from_zero(monkeypatch):
    from quant_studio import etf_daily_web

    monkeypatch.setattr(
        etf_daily_web,
        "inspect_daily",
        lambda: {
            "data": {
                "covered_through": "2026-01-01",
                "captured_at": "2026-01-02",
                "fresh": False,
            },
            "expected_session": "2026-01-02",
            "latest": None,
            "last_success": None,
            "forward": None,
            "execution": {"available": False},
        },
    )
    html = today_body("csrf-test")
    assert "需要更新" in html and "不按零处理" in html and "尚无成功观察" in html


def test_binary_native_artifact_tamper_is_detected(tmp_path):
    from quant_studio.result_integrity import output_hashes, verify_result

    output = tmp_path / "strategy-output"
    output.mkdir()
    path = output / "positions.parquet"
    path.write_bytes(b"native ledger bytes")
    hashes = output_hashes(tmp_path)
    assert "strategy-output/positions.parquet" in hashes
    path.write_bytes(b"corrupted ledger")
    with pytest.raises(QuantStudioError, match="改变"):
        verify_result(tmp_path, {"output_hashes": hashes})
