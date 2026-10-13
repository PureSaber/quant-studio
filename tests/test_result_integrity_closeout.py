import hashlib
import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.desk import list_runs
from quant_studio.experiments import ExperimentStore
from quant_studio.runner import run
from quant_studio.server import render_run, resolve_run_asset
from quant_studio.workbench_web import experiment_body, experiments_body


@pytest.mark.parametrize("filename", ["nav.csv", "report.html", "config.json"])
def test_changed_output_blocks_page_comparison_download_and_assistant(
    tmp_path, filename
):
    one = run("synthetic-demo", execute=True, runs_root=tmp_path)
    two = run("synthetic-demo", execute=True, runs_root=tmp_path)
    assert one.status == two.status == "succeeded"
    paths = one.output_hashes
    if filename == "config.json":
        filename = next(name for name in paths if name.startswith("config."))
    target = one.run_dir / filename
    target.write_bytes(target.read_bytes() + b"\n ")
    store = ExperimentStore(tmp_path)
    assert not store.compare([one.run_id, two.run_id])["comparable"]
    assert not store.evidence_summary(one.run_id)["verified"]
    assert "结果文件已改变" in render_run(one.run_dir)
    assert "净值曲线" not in render_run(one.run_dir)
    assert (
        next(r for r in list_runs(tmp_path) if r["run_id"] == one.run_id)["has_nav"]
        == "0"
    )
    with pytest.raises(QuantStudioError):
        resolve_run_asset(tmp_path, one.run_id, "nav.csv")


@pytest.mark.parametrize("annotation", ["{bad", "{}", "[]", '{"title":"x"}'])
def test_bad_annotations_preserve_success_and_failure_history(tmp_path, annotation):
    one = run("synthetic-demo", execute=True, runs_root=tmp_path)
    two = run("synthetic-demo", execute=True, runs_root=tmp_path)
    result = two.run_dir / "result.json"
    content = json.loads(result.read_text())
    result.write_text(json.dumps(content | {"status": "failed"}))
    store = ExperimentStore(tmp_path)
    (store.notes / f"{two.run_id}.json").write_text(annotation)
    rows = store.list()
    assert {row["run_id"] for row in rows} == {one.run_id, two.run_id}
    assert next(row for row in rows if row["run_id"] == two.run_id)["annotation_error"]
    assert "备注无法读取" in experiments_body(tmp_path, "", {})
    assert "备注无法读取" in experiment_body(tmp_path, "", two.run_id)
    with pytest.raises(QuantStudioError, match="备注无法读取"):
        store.annotate(two.run_id, "overwrite", "", expected="")


def test_legacy_run_remains_visible_but_cannot_be_recertified_by_reading(tmp_path):
    result = run("synthetic-demo", execute=True, runs_root=tmp_path)
    path = result.run_dir / "result.json"
    content = json.loads(path.read_text())
    content.pop("output_hashes")
    path.write_text(json.dumps(content))
    before = path.read_bytes()
    assert len(ExperimentStore(tmp_path).list()) == 1
    assert not ExperimentStore(tmp_path).evidence_summary(result.run_id)["verified"]
    assert "历史结果缺少产物哈希清单" in render_run(result.run_dir)
    assert path.read_bytes() == before


def test_comparison_and_assistant_recheck_the_native_verification_digest(tmp_path):
    run_result = run("synthetic-demo", execute=True, runs_root=tmp_path)
    path = run_result.run_dir / "result.json"
    evidence = run_result.run_dir / "verification.json"
    evidence.write_text("{}")
    payload = json.loads(path.read_text())
    payload.update(
        verification_status="pass",
        verification_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )
    path.write_text(json.dumps(payload))
    store = ExperimentStore(tmp_path)
    summary = store.evidence_summary(run_result.run_id)
    assert summary["verified"] and summary["metric_units"] == "ratio"
    assert summary["configuration"] and "fees" in summary["unavailable"]
    json.dumps(summary)  # Context sent to the assistant must be JSON serializable.
    evidence.write_text('{"changed": true}')
    assert "账本核验记录已改变" in store.measure(run_result.run_id)["error"]
    assert not store.evidence_summary(run_result.run_id)["verified"]
    with pytest.raises(QuantStudioError, match="账本核验记录已改变"):
        resolve_run_asset(tmp_path, run_result.run_id, "nav.csv")


@pytest.mark.parametrize("filename", ["request.json", "config.yaml"])
def test_comparison_checks_integrity_before_parsing_damaged_metadata(
    tmp_path, filename
):
    one = run("synthetic-demo", execute=True, runs_root=tmp_path)
    two = run("synthetic-demo", execute=True, runs_root=tmp_path)
    (one.run_dir / filename).write_text("{broken")
    store = ExperimentStore(tmp_path)
    assert not store.compare([one.run_id, two.run_id])["comparable"]
    assert "结果文件已改变" in store.evidence_summary(one.run_id)["error"]
