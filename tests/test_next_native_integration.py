"""Cross-repository adapters tested in explicitly selected independent environments."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from quant_studio.data_maintenance import DataMaintenance
from quant_studio.datasets import DatasetStore
from quant_studio.environment_delivery import DeliveryStore
from quant_studio.project_assistant import AssistantStore
from quant_studio.runner import _execute_subprocess
from tests.test_notebook_research import configure
from tests.test_notebook_templates import saved_template


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_INTAKE_IMPORT_PYTHON"), reason="independent QDK"
)
def test_etf_native_build_update_and_old_snapshot_preservation(tmp_path, monkeypatch):
    python = os.environ["QUANT_TEST_INTAKE_IMPORT_PYTHON"]
    source_repo = Path(os.environ["QUANT_TEST_INTAKE_SOURCE"])
    source = tmp_path / "fixture"
    subprocess.run(
        [
            python,
            "-X",
            "utf8",
            "-c",
            "from pathlib import Path; import pandas as pd,sys; "
            "from tests.test_research_dataset import _source; "
            "_source(Path(sys.argv[1]), pd.DatetimeIndex("
            "['2026-01-15','2026-01-16','2026-01-19','2026-01-20','2026-01-21']),"
            "[10,10,9,9.1,9.2],[9,9,9,9.1,9.2])",
            str(source),
        ],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(
        "quant_studio.intake_tools.IntakeWorkspace.runtime",
        lambda self: (python, source_repo),
    )

    def local_source(argv, **kwargs):
        # Supply declared synthetic exports to the real QDK CLI; no provider mock.
        argv += [
            "--source-dir",
            str(source),
            "--source-uri",
            "fixture:integration",
            "--source-version",
            "v1",
            "--license-note",
            "synthetic test",
            "--captured-at",
            "2026-01-22T00:00:00Z",
        ]
        return _execute_subprocess(argv, **kwargs)

    monkeypatch.setattr("quant_studio.runner._execute_subprocess", local_source)
    store = DataMaintenance(tmp_path / "runs")
    linked = store.create_etf("合成ETF接口验收", ["510300"], "2026-01-15")
    assert (
        store.execute(store.request(linked["id"], "2026-01-20"), None)["status"]
        == "succeeded"
    )
    datasets = DatasetStore(tmp_path / "runs")
    old = datasets.list()[0]
    assert (
        store.execute(store.request(linked["id"], "2026-01-21"), None)["status"]
        == "succeeded"
    )
    new = datasets.list()[0]
    assert new["parent"] == old["id"]
    assert datasets.verify(old["id"])["valid"]
    assert datasets.verify(new["id"])["valid"]
    assert new["quality"]["update_evidence"]["mode"] == "overlap_refresh"
    Path(old["path"]).rename(Path(old["path"]).with_name("moved-old-snapshot"))
    assert not datasets.verify(old["id"])["valid"]


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_AGENT_PYTHON"), reason="independent Agent"
)
def test_agent_native_preview_with_notebook_never_modifies_draft(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    project, draft, _ = saved_template(tmp_path)
    before = draft.read_bytes()
    monkeypatch.setattr(
        "quant_studio.project_assistant.configured_python",
        lambda _: os.environ["QUANT_TEST_AGENT_PYTHON"],
    )
    store = AssistantStore(tmp_path)
    record = store.prepare(
        project["id"], project["revision"], "如何改变窗口", include_notebook=True
    )
    result = store.execute({"project_id": project["id"], "id": record["id"]}, None)
    assert result["status"] == "succeeded"
    answer = json.loads(
        (store.directory(project["id"], record["id"]) / "answer.json").read_text(
            "utf-8"
        )
    )
    assert answer["patches"] == []
    assert draft.read_bytes() == before


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_WORKSPACE_PYTHON"), reason="independent Workspace"
)
def test_workspace_doctor_runs_native_and_preserves_missing_environment(
    tmp_path, monkeypatch
):
    python = os.environ["QUANT_TEST_WORKSPACE_PYTHON"]
    source_repo = Path(os.environ["QUANT_TEST_WORKSPACE_SOURCE"])
    repo = tmp_path / "example"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname="sample"\nversion="0.1"\n')
    (repo / "requirements.lock").write_text("packaging==1.0\n")
    for args in [
        ("init",),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "test@example.invalid"),
        ("add", "."),
        ("commit", "-m", "fixture"),
    ]:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    workspace = tmp_path / "workspace.yaml"
    workspace.write_text("root: .\nprojects:\n  example: {repo: example}\n")
    profile = tmp_path / "profile.json"
    subprocess.run(
        [
            python,
            "-I",
            "-m",
            "quant_workspace.cli",
            "--config",
            str(workspace),
            "runtime-profile",
            "--projects",
            "example",
            "--out",
            str(profile),
        ],
        cwd=source_repo,
        check=True,
        capture_output=True,
    )
    config = tmp_path / "delivery.json"
    config.write_text(
        json.dumps(
            {
                "python": python,
                "source_root": str(tmp_path),
                "workspace_config": str(workspace),
                "profile": str(profile),
                "state_dir": str(tmp_path / "releases"),
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_DELIVERY_CONFIG", str(config))
    store = DeliveryStore(tmp_path / "runs")
    record = store.prepare("doctor")
    assert store.execute(record["id"], None)["status"] == "failed"
    result = json.loads(
        (store.directory(record["id"]) / "result.json").read_text("utf-8")
    )
    assert result["status"] == "blocked"
    assert not (repo / ".venv").exists()


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_WORKSPACE_PYTHON"), reason="independent Workspace"
)
def test_workspace_native_candidate_cas_and_transfer(tmp_path, monkeypatch):
    python = os.environ["QUANT_TEST_WORKSPACE_PYTHON"]
    source_repo = Path(os.environ["QUANT_TEST_WORKSPACE_SOURCE"])

    def fixture_command(program):
        subprocess.run(
            [python, "-X", "utf8", "-c", program, str(tmp_path)],
            cwd=source_repo,
            check=True,
            capture_output=True,
        )

    fixture_command(
        "from pathlib import Path; import sys; "
        "from tests.test_delivery import release_fixture,make_candidate; "
        "from tests.test_transfer import migration_fixture; "
        "from quant_workspace.transfer import create_transfer_package; "
        "base=Path(sys.argv[1]); f=release_fixture.__wrapped__(base/'release'); "
        "make_candidate(f,base/'candidate.json'); "
        "t=migration_fixture.__wrapped__(base/'transfer'); "
        "create_transfer_package(t['spec_path'],t['root'],base/'migration.zip')"
    )
    root = tmp_path / "release/source"
    config = tmp_path / "delivery.json"
    config.write_text(
        json.dumps(
            {
                "python": python,
                "source_root": str(root),
                "workspace_config": str(root / "workspace.yaml"),
                "profile": str(tmp_path / "release/profile.json"),
                "state_dir": str(tmp_path / "state"),
                "candidates": {
                    "test": {
                        "candidate": str(tmp_path / "candidate.json"),
                        "prepared_root": str(tmp_path / "prepared"),
                        "evidence": str(tmp_path / "accepted.json"),
                    }
                },
                "transfers": {
                    "test": {
                        "archive": str(tmp_path / "migration.zip"),
                        "destination": str(tmp_path / "restored"),
                    }
                },
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_DELIVERY_CONFIG", str(config))
    store = DeliveryStore(tmp_path / "runs")

    def execute(action):
        record = store.prepare(action, "test")
        assert store.execute(record["id"], None)["status"] == "succeeded"
        return json.loads(
            (store.directory(record["id"]) / "result.json").read_text("utf-8")
        )

    assert execute("prepare-preview")["executed"] is False
    assert not (tmp_path / "prepared").exists()
    # Assemble an offline fixture environment; do not resolve or install packages.
    fixture_command(
        "from pathlib import Path; import sys; "
        "from quant_workspace.delivery import prepare_candidate; "
        "from tests.test_delivery import create_environment; "
        "base=Path(sys.argv[1]); "
        "prepare_candidate(base/'candidate.json',base/'release/source',"
        "base/'prepared',execute=True); "
        "create_environment(base/'prepared/sample')"
    )
    assert execute("accept")["status"] == "accepted"
    stale = store.prepare("activate", "test")
    assert execute("activate")["manual_service_activation_required"] is True
    assert store.execute(stale["id"], None)["status"] == "failed"
    assert execute("rollback")["receipt_record"]["service_restarted"] is False
    assert execute("verify-transfer")["manifest_sha256"]
    assert execute("restore-transfer")["status"] == "needs_configuration"
    assert (tmp_path / "restored/runs/result.json").read_bytes() == (
        tmp_path / "transfer/origin/runs/result.json"
    ).read_bytes()
