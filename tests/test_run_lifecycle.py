import json

import pytest

from quant_studio.desk import list_runs
from quant_studio.runner import preview, run
from quant_studio.server import render_run


def test_running_record_exists_before_execution_and_stays_inspectable_after_interrupt(
    tmp_path, monkeypatch
):
    def interrupt(template, rendered, directory):
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "running"
        raise KeyboardInterrupt("injected interruption")

    monkeypatch.setattr("quant_studio.synthetic.execute_synthetic", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run("synthetic-demo", execute=True, runs_root=tmp_path, run_id="interrupted")
    records = list_runs(tmp_path)
    assert len(records) == 1 and records[0]["status"] == "running"
    page = render_run(tmp_path / "interrupted")
    assert "尚未完成" in page and "新记录" in page
    assert "净值曲线" not in page


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "non_object"])
def test_damaged_or_missing_completion_record_is_visible_and_read_only(
    tmp_path, damage
):
    result = preview("synthetic-demo", runs_root=tmp_path, run_id="incomplete")
    path = result.run_dir / "result.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text(
            "[1]" if damage == "non_object" else "{unfinished", encoding="utf-8"
        )
    (result.run_dir / "nav.csv").write_text("date,nav\n2025-01-01,1\n2025-01-02,2\n")
    original = {p.name: p.read_bytes() for p in result.run_dir.iterdir()}
    records = list_runs(tmp_path)
    assert records[0]["status"] == "incomplete"
    assert records[0]["has_nav"] == records[0]["has_report"] == "0"
    page = render_run(result.run_dir)
    assert "未完成" in page and "净值曲线" not in page
    assert "配置与执行命令" in page
    assert {p.name: p.read_bytes() for p in result.run_dir.iterdir()} == original


def test_result_json_replacement_failure_preserves_previous_complete_json(
    tmp_path, monkeypatch
):
    from quant_studio.runner import _write_json

    path = tmp_path / "result.json"
    _write_json(path, {"status": "running"})

    def fail(*args, **kwargs):
        raise OSError("injected atomic replace interruption")

    monkeypatch.setattr("quant_studio.runner.os.replace", fail)
    with pytest.raises(OSError):
        _write_json(path, {"status": "succeeded"})
    assert json.loads(path.read_text()) == {"status": "running"}
    assert list(tmp_path.iterdir()) == [path]
