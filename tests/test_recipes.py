import copy
import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.recipes import (
    RecipeStore,
    load_input_config,
    parse_config,
    recipe_template,
)
from quant_studio.research_web import _read_fields
from quant_studio.runner import preview
from quant_studio.server import render_run
from quant_studio.templates import load_template, template_ids
from tests.test_server import _request, _start_http_server


def test_revision_copy_conflict_and_exact_execution(tmp_path):
    store = RecipeStore(tmp_path)
    config = copy.deepcopy(load_template("synthetic-demo").base_config)
    config["initial_capital"] = 43210
    first = store.save("My study", "synthetic-demo", config)
    config["initial_capital"] = 76543
    second = store.save(
        "Updated",
        "synthetic-demo",
        config,
        recipe_id=first["id"],
        expected=first["revision"],
    )
    assert (
        store.get(first["id"], first["revision"])["config"]["initial_capital"] == 43210
    )
    assert store.get(first["id"])["revision"] == second["revision"]
    with pytest.raises(QuantStudioError, match="已更新"):
        store.save(
            "Stale",
            "synthetic-demo",
            config,
            recipe_id=first["id"],
            expected=first["revision"],
        )
    clone = store.import_document(json.dumps(first), name="Copy")
    assert clone["id"] != first["id"]
    assert clone["config"] == first["config"]
    result = preview(recipe_template(first), runs_root=tmp_path / "runs")
    import yaml

    assert (
        yaml.safe_load((result.run_dir / "config.yaml").read_text())["initial_capital"]
        == 43210
    )
    request = json.loads((result.run_dir / "request.json").read_text())
    assert request["recipe"]["revision"] == first["revision"]
    page = render_run(result.run_dir)
    assert "My study" in page and "模板默认" not in page
    assert f"/research/{first['id']}" in page
    assert "/templates/synthetic-demo" not in page


@pytest.mark.parametrize(
    "text",
    ["x: &x [*x]", "x: .nan", "[]", "a: 1\na: 2", "!!python/object:os.system {}"],
)
def test_config_rejects_ambiguous_or_unsafe_inputs(text):
    with pytest.raises(QuantStudioError):
        parse_config(text)


def test_tampering_path_and_unknown_native_field_rejected(tmp_path):
    store = RecipeStore(tmp_path)
    with pytest.raises(QuantStudioError):
        store.get("../escape")
    config = copy.deepcopy(load_template("synthetic-demo").base_config)
    with pytest.raises(QuantStudioError, match="未知"):
        store.save("Typo", "synthetic-demo", config | {"initial_captial": 1})
    record = store.save("Study", "synthetic-demo", config)
    record["config"]["initial_capital"] = 1
    with pytest.raises(QuantStudioError, match="校验"):
        store.import_document(json.dumps(record))


def test_cli_knobs_frozen_and_hk_not_fixed_to_five_stocks(tmp_path):
    store = RecipeStore(tmp_path)
    template = load_template("hk-equity-daily")
    config = copy.deepcopy(template.base_config)
    config["instruments"] = config["instruments"][:2]
    config["top_n"] = 1
    config["data_end"] = "2025-11-14"
    record = store.save("Two stocks", template.id, config)
    assert recipe_template(record).base_config == config


@pytest.mark.parametrize("template_id", template_ids())
def test_every_builtin_can_be_saved_with_effective_defaults(tmp_path, template_id):
    template = load_template(template_id)
    record = RecipeStore(tmp_path).save(
        template.title, template.id, template.base_config
    )
    assert recipe_template(record).id == template.id


def test_ashare_result_column_tracks_custom_quantile_count(tmp_path):
    template = load_template("a-share-four-factor")
    config = copy.deepcopy(template.base_config)
    config["quantiles"] = 2
    record = RecipeStore(tmp_path).save("Two groups", template.id, config)
    assert recipe_template(record).metadata["nav_column"] == "Q2"


def test_integer_spelled_defaults_still_allow_fractional_number_parameters():
    assert _read_fields(
        {"cost_multiplier": 1}, {"cfg:/cost_multiplier": ["0.5"]}, "cfg:"
    ) == {"cost_multiplier": 0.5}


def test_external_yaml_is_copied_into_saved_plan_and_each_run(tmp_path):
    source = tmp_path / "research.yaml"
    source.write_text(
        "input:\n  path: prices.csv\nwalk_forward:\n  formation_bars: 120\n"
    )
    frozen = load_input_config("stat-arb-research", source)
    template = load_template("stat-arb-research")
    record = RecipeStore(tmp_path).save(
        "Frozen",
        template.id,
        template.base_config,
        snapshot=str(source),
        input_config=frozen,
    )
    source.write_text("broken: later edit")
    result = preview(
        recipe_template(record), runs_root=tmp_path / "runs", snapshot=source
    )
    copied = parse_config((result.run_dir / "source-config.yaml").read_text())
    assert copied["walk_forward"]["formation_bars"] == 120
    assert copied["input"]["path"] == str(tmp_path / "prices.csv")
    assert str(result.run_dir / "source-config.yaml") in result.argv


def test_http_plan_form_import_export_and_queue(tmp_path):
    server, thread = _start_http_server(tmp_path)
    authority = f"localhost:{server.server_port}"
    try:
        status, _, page = _request(
            server, "GET", "/research/new?template=synthetic-demo", host=authority
        )
        assert status == 200 and b"cfg:/initial_capital" in page
        config = copy.deepcopy(load_template("synthetic-demo").base_config)
        config["initial_capital"] = 54321
        fields = {
            "_csrf_token": "test-only-csrf-token",
            "template_id": "synthetic-demo",
            "name": "A custom study",
            "action": "import_config",
            "native_config": json.dumps(config),
        }
        status, headers, _ = _request(
            server,
            "POST",
            "/research/new",
            host=authority,
            origin=f"http://{authority}",
            fields=fields,
        )
        assert status == 303
        url = headers["Location"]
        status, _, body = _request(server, "GET", url + "/export", host=authority)
        assert status == 200
        record = json.loads(body)
        assert record["config"]["initial_capital"] == 54321
        status, _, _ = _request(
            server,
            "POST",
            url,
            host=authority,
            origin=f"http://{authority}",
            fields={
                "_csrf_token": "test-only-csrf-token",
                "action": "execute_saved",
                "revision": record["revision"],
            },
        )
        assert status == 303
        manager = server.RequestHandlerClass.job_manager
        assert manager.list()[0]["recipe"]["revision"] == record["revision"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
