"""Real installed CLI/worker integration; optional six-repository profile required."""

import json
import sys

import pytest

pytest.importorskip("quant_pipeline.durable_batches")
pytest.importorskip("quant_data_kit.shared_snapshots")
pytest.importorskip("quant_lab.rolling_research")

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from quant_data_kit.artifacts import ArtifactStore
from quant_data_kit.shared_snapshots import SnapshotStore
from quant_factors.contracts_v2 import (
    AsOfSpec,
    FactorDependency,
    FactorSpec,
    FrequencySpec,
)
from quant_pipeline.durable_jobs import JobStore
from quant_pipeline.durable_research import retain_artifacts
from quant_pipeline.durable_worker import Worker

from quant_studio.durable_research import DurableResearchClient


def test_real_studio_cli_batch_worker_and_shared_snapshot(tmp_path, monkeypatch):
    stamps = pd.date_range("2024-01-01", periods=30, tz="UTC")
    source = tmp_path / "input.parquet"
    pq.write_table(
        pa.Table.from_pydict(
            {
                "instrument_id": ["SYNTHETIC"] * 30,
                "event_time": stamps,
                "received_at": stamps,
                "available_at": stamps,
                "sequence": list(range(30)),
                "event_id": [f"event-{i}" for i in range(30)],
                "close": [100.0 + i for i in range(30)],
            }
        ),
        source,
    )
    artifacts = ArtifactStore(tmp_path / "cas")
    snapshot = (
        SnapshotStore(artifacts)
        .publish(
            {"prices": source},
            dataset="synthetic",
            version="1",
            owner="fixture",
            temporal={"calendar": "synthetic", "timezone": "UTC"},
            date_column="event_time",
            symbol_column="instrument_id",
            available_at_column="available_at",
        )
        .artifact.to_dict()
    )
    frequency = FrequencySpec(
        "daily",
        "session_bar",
        "365",
        "synthetic",
        "v1",
        None,
        "trading_day",
        None,
        None,
        None,
    )
    factor = FactorSpec(
        "momentum",
        "1.0.0",
        "momentum",
        "bar",
        [FactorDependency("market", "close", "available_at")],
        3,
        "float64",
        False,
        "null",
    )
    request = {
        "schema_version": "quant.job-request/v1",
        "kind": "rolling_research",
        "project_id": "research-project",
        "run_id": "admin",
        "inputs": [snapshot],
        "timeout_seconds": 60,
        "max_attempts": 2,
        "payload": {
            "schema_version": "quant.rolling-research/v1",
            "snapshot": snapshot,
            "frequency": frequency.to_contract(),
            "factors": [factor.to_contract()],
            "as_of": AsOfSpec("source_available_at", None).to_contract(),
            "universe": ["SYNTHETIC"],
        },
    }
    config = {
        "schema_version": "quant-studio.durable-research/v1",
        "python": sys.executable,
        "database": str(tmp_path / "jobs.db"),
        "artifact_root": str(artifacts.root),
        "cache_database": str(tmp_path / "cache.db"),
        "requests_root": str(tmp_path / "requests"),
        "snapshots": {"fixed": {"title": "Synthetic", "request": request}},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    client = DurableResearchClient(path)
    batch = client.submit_batch("fixed", "3,5", "a" * 32, client.revision("fixed"))
    assert len(batch["members"]) == 2
    assert len(client.batches()) == 1
    monkeypatch.setenv("QUANT_RESEARCH_STORE_ROOT", str(artifacts.root))
    monkeypatch.setenv("QUANT_RESEARCH_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.delenv("QUANT_RESEARCH_REGISTRY_DB", raising=False)
    worker = Worker(
        JobStore(tmp_path / "jobs.db"),
        tmp_path / "attempts",
        {"rolling_research": "quant_lab.rolling_research:worker_handler"},
        artifact_guard=retain_artifacts,
    )
    assert worker.run_once()["status"] == "succeeded"
    assert worker.run_once()["status"] == "succeeded"
    final = client.batch("get", batch["batch_id"])
    assert final["status"] == "succeeded" and final["counts"]["succeeded"] == 2
    assert all(
        member["job"]["request"]["inputs"] == [snapshot] for member in final["members"]
    )
    for member in final["members"]:
        assert len(client.query("attempts", member["job"]["job_id"])) == 1
