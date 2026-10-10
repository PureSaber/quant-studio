import copy
import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.experiments import ExperimentStore, configuration_diff
from quant_studio.recipes import RecipeStore
from quant_studio.runner import run
from quant_studio.templates import load_template
from tests.test_server import _request, _start_http_server


def test_history_notes_preserve_old_revision_and_detect_stale_save(tmp_path):
    store = RecipeStore(tmp_path)
    base = copy.deepcopy(load_template("synthetic-demo").base_config)
    first = store.save("均线研究", "synthetic-demo", base, note="初始方案")
    second = store.save(
        "均线研究",
        "synthetic-demo",
        base | {"initial_capital": 12345},
        recipe_id=first["id"],
        expected=first["revision"],
        note="调整资金",
    )
    assert [r["revision"] for r in store.history(first["id"])] == [
        first["revision"],
        second["revision"],
    ]
    assert store.get(first["id"], first["revision"])["note"] == "初始方案"
    diff = configuration_diff(first, second)
    assert any(
        row["path"] == "config.initial_capital" and row["after"] == 12345
        for row in diff
    )
    with pytest.raises(QuantStudioError, match="已更新"):
        store.save(
            "旧页面",
            "synthetic-demo",
            base,
            recipe_id=first["id"],
            expected=first["revision"],
        )


def test_experiment_search_annotation_conflicts_and_compare(tmp_path):
    first = run(
        "synthetic-demo", {"initial_capital": 10000}, execute=True, runs_root=tmp_path
    )
    second = run(
        "synthetic-demo", {"initial_capital": 20000}, execute=True, runs_root=tmp_path
    )
    store = ExperimentStore(tmp_path)
    note = store.annotate(first.run_id, "资金测试", "观察费用变化", expected="")
    assert store.list(query="费用")[0]["run_id"] == first.run_id
    with pytest.raises(QuantStudioError, match="已更新"):
        store.annotate(first.run_id, "覆盖", "旧页面", expected="")
    assert store.annotation(first.run_id) == note
    comparison = store.compare([first.run_id, second.run_id])
    assert comparison["comparable"]
    assert len(comparison["runs"]) == 2
    assert comparison["runs"][0]["return"] == comparison["runs"][1]["return"]
    with pytest.raises(QuantStudioError):
        store.compare([first.run_id, "../outside"])
    result = tmp_path / second.run_id / "result.json"
    data = json.loads(result.read_text())
    data["status"] = "failed"
    result.write_text(json.dumps(data))
    comparison = store.compare([first.run_id, second.run_id])
    assert not comparison["comparable"]
    assert comparison["runs"][1]["return"] is None


def test_comparison_does_not_merge_different_input_sources(tmp_path):
    one = run("synthetic-demo", execute=True, runs_root=tmp_path)
    two = run("synthetic-demo", execute=True, runs_root=tmp_path)
    for result, provider in [(one, "synthetic"), (two, "real-provider")]:
        (tmp_path / result.run_id / "provenance.json").write_text(
            json.dumps({"input": {"provider": provider}}), encoding="utf-8"
        )
    comparison = ExperimentStore(tmp_path).compare([one.run_id, two.run_id])
    assert not comparison["comparable"]
    assert "数据性质不同" in comparison["reasons"]


def test_experiment_chart_labels_do_not_call_an_experiment_a_benchmark(tmp_path):
    from quant_studio.workbench_web import compare_body

    one = run("synthetic-demo", execute=True, runs_root=tmp_path)
    two = run("synthetic-demo", execute=True, runs_root=tmp_path)
    page = compare_body(tmp_path, [one.run_id, two.run_id])
    assert "实验累计收益" in page
    assert "策略与基准累计收益" not in page


def test_http_history_dataset_editor_notes_and_comparison(tmp_path):
    store = RecipeStore(tmp_path)
    first = store.save(
        "研究一", "synthetic-demo", {"initial_capital": 10000}, note="起点"
    )
    store.save(
        "研究一",
        "synthetic-demo",
        {"initial_capital": 20000},
        recipe_id=first["id"],
        expected=first["revision"],
        note="资金翻倍",
    )
    run_one = run("synthetic-demo", execute=True, runs_root=tmp_path)
    run_two = run("synthetic-demo", execute=True, runs_root=tmp_path)
    server, thread = _start_http_server(tmp_path)
    host = f"127.0.0.1:{server.server_port}"
    try:
        paths = [
            "/datasets",
            "/experiments",
            "/operations",
            f"/research/{first['id']}/history",
            f"/experiments/{run_one.run_id}",
            f"/experiments/compare?run={run_one.run_id}&run={run_two.run_id}",
            "/research/new?template=hk-equity-daily",
        ]
        for path in paths:
            status, _, body = _request(server, "GET", path, host=host)
            assert status == 200, (path, body)
        fields = {
            "_csrf_token": "test-only-csrf-token",
            "title": "实验命名",
            "note": "已比较",
            "revision": "",
        }
        path = f"/experiments/{run_one.run_id}/note"
        assert (
            _request(
                server, "POST", path, host=host, origin=f"http://{host}", fields=fields
            )[0]
            == 303
        )
        assert (
            _request(
                server, "POST", path, host=host, origin=f"http://{host}", fields=fields
            )[0]
            == 400
        )
        assert (
            _request(
                server,
                "POST",
                path,
                host=host,
                origin="http://evil.invalid",
                fields=fields,
            )[0]
            == 403
        )
        assert ExperimentStore(tmp_path).annotation(run_one.run_id)["note"] == "已比较"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_friendly_fields_keep_advanced_contract_separate():
    from quant_studio.research_web import _fields, _read_fields

    config = {
        "schema": "native/v1",
        "top_n": 2,
        "instruments": [{"symbol": "00700", "name": "Tencent", "lot_size": 100}],
    }
    markup = _fields(config, "cfg:")
    assert "高级参数与数据口径" in markup
    assert "<label>持仓数量<small>/top_n" not in markup
    assert 'class="recipe-field wide"' in markup
    assert "<label>股票名单与交易规则" not in markup
    data = {
        "cfg:/schema": ["native/v1"],
        "cfg:/top_n": ["3"],
        "cfg:/instruments": [json.dumps(config["instruments"])],
    }
    assert _read_fields(config, data, "cfg:")["top_n"] == 3
