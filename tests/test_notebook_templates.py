import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.notebook_templates import NotebookTemplateStore, parameter_values
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from tests.test_notebook_research import configure

PARAMETERS = {
    "window": {
        "label": "观察窗口",
        "type": "integer",
        "default": 20,
        "min": 1,
        "max": 120,
    },
    "label": {"label": "实验备注", "type": "string", "default": "基准"},
}


def saved_template(root):
    project = ProjectStore(root).save("源研究", "窗口改变会怎样？")
    draft = NotebookStore(root).initialize(project["id"])
    nb = json.loads(draft.read_text(encoding="utf-8"))
    nb["cells"][-1]["source"] = "print(parameters['window'], parameters['label'])"
    nb["cells"][-1]["outputs"] = [
        {"output_type": "stream", "name": "stdout", "text": "旧输出"}
    ]
    draft.write_text(json.dumps(nb), encoding="utf-8")
    template = NotebookTemplateStore(root).save(
        project["id"], project["revision"], "窗口研究", PARAMETERS
    )
    return project, draft, template


def test_saved_template_is_immutable_and_reuse_isolated(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    original, draft, template = saved_template(tmp_path)
    before = draft.read_bytes()
    store = NotebookTemplateStore(tmp_path)
    clone = store.instantiate(
        template["id"],
        template["revision"],
        "试验A",
        [],
        {"window": 5, "label": "新备注"},
    )
    assert clone["id"] != original["id"]
    assert draft.read_bytes() == before
    notebooks = NotebookStore(tmp_path)
    copied = json.loads(
        (notebooks.workspace(clone["id"]) / "research.ipynb").read_text(
            encoding="utf-8"
        )
    )
    assert all(not c.get("outputs") for c in copied["cells"])
    notebooks.initialize(clone["id"])
    record = notebooks.prepare(clone["id"], clone["revision"])
    context = json.loads(
        notebooks.artifact(clone["id"], record["id"], "inputs.json").read_text(
            encoding="utf-8"
        )
    )
    assert context["parameters"] == {"window": 5, "label": "新备注"}
    assert context["template"]["revision"] == template["revision"]
    assert store.get(template["id"])["notebook"] == template["notebook"]
    with pytest.raises(QuantStudioError, match="版本"):
        store.instantiate(template["id"], "0" * 64, "过期", [], {})


@pytest.mark.parametrize(
    "values",
    [
        {"window": True},
        {"window": 0},
        {"window": 1.5},
        {"window": float("nan")},
        {"extra": 1},
    ],
)
def test_invalid_parameter_values_rejected(values):
    with pytest.raises(QuantStudioError):
        parameter_values(PARAMETERS, values)


@pytest.mark.parametrize(
    "schema",
    [
        {"x": {"label": "值", "type": [], "default": 1}},
        {"x": {"label": "值", "type": "number", "default": 10**400}},
    ],
)
def test_malformed_schema_is_a_user_error(schema):
    with pytest.raises(QuantStudioError):
        parameter_values(schema, {})


def test_parameter_strings_are_data_and_not_interpolated(tmp_path):
    _, _, template = saved_template(tmp_path)
    attack = "\"; __import__('pathlib').Path('unexpected').touch(); #"
    project = NotebookTemplateStore(tmp_path).instantiate(
        template["id"], template["revision"], "文字参数", [], {"label": attack}
    )
    workspace = NotebookStore(tmp_path).workspace(project["id"])
    assert attack not in (workspace / "research.ipynb").read_text(encoding="utf-8")
    assert (
        json.loads((workspace / "inputs.json").read_text(encoding="utf-8"))[
            "parameters"
        ]["label"]
        == attack
    )


def test_template_corruption_rejected(tmp_path):
    _, _, template = saved_template(tmp_path)
    path = tmp_path / ".notebook-templates" / f"{template['id']}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["name"] = "被改写"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(QuantStudioError, match="完整性"):
        NotebookTemplateStore(tmp_path).get(template["id"])


def test_builtin_templates_can_be_saved_and_declare_metrics(tmp_path, monkeypatch):
    from tests.test_notebook_intake import _intake_dataset

    item = _intake_dataset(tmp_path)
    project = ProjectStore(tmp_path).save(
        "收益研究", "收益缺失如何？", datasets=[item["id"]]
    )
    notebooks = NotebookStore(tmp_path)
    monkeypatch.setattr(notebooks, "_install_intake_reader", lambda *args: None)
    notebooks.initialize(project["id"], template="price-return-missingness")
    saved = NotebookTemplateStore(tmp_path).save(
        project["id"], project["revision"], "价格研究", {}
    )
    source = "\n".join("".join(c["source"]) for c in saved["notebook"]["cells"])
    assert item["id"] not in source
    assert "research_metrics" in source


def test_failed_template_initialization_leaves_no_project_or_draft(
    tmp_path, monkeypatch
):
    original, draft, template = saved_template(tmp_path)
    before = draft.read_bytes()

    def fail_initialize(self, identifier, revision=None, template=None):
        assert (self.workspace(identifier) / "template.json").is_file()
        raise QuantStudioError("QDK环境不可用")

    monkeypatch.setattr(NotebookStore, "initialize", fail_initialize)
    with pytest.raises(QuantStudioError, match="QDK环境不可用"):
        NotebookTemplateStore(tmp_path).instantiate(
            template["id"], template["revision"], "创建失败", [], {}
        )
    assert [p["id"] for p in ProjectStore(tmp_path).list()] == [original["id"]]
    assert [p.name for p in (tmp_path / ".notebooks").iterdir()] == [original["id"]]
    assert draft.read_bytes() == before
    assert NotebookTemplateStore(tmp_path).get(template["id"]) == template


def test_corrupt_template_document_returns_domain_error(tmp_path):
    _, _, template = saved_template(tmp_path)
    path = tmp_path / ".notebook-templates" / f"{template['id']}.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(QuantStudioError):
        NotebookTemplateStore(tmp_path).get(template["id"])


@pytest.mark.parametrize("bound", ["min", "max"])
def test_integer_bounds_reject_fractional_form_step_base(bound):
    # HTML number inputs use min as their step base. min=0.5 with step=1
    # otherwise makes every integer invalid in the browser despite server acceptance.
    schema = {
        "window": {
            "label": "窗口",
            "type": "integer",
            "default": 1,
            bound: 0.5 if bound == "min" else 2.5,
        }
    }
    with pytest.raises(QuantStudioError, match="整数参数"):
        parameter_values(schema, {})


def test_failed_initialization_preserves_concurrent_edit(tmp_path, monkeypatch):
    original, _, template = saved_template(tmp_path)
    changed = []

    def edit_then_fail(self, identifier, revision=None, template=None):
        changed.append(
            ProjectStore(tmp_path).save(
                "另一次编辑", "保留新输入", project_id=identifier, expected=revision
            )
        )
        raise QuantStudioError("初始化失败")

    monkeypatch.setattr(NotebookStore, "initialize", edit_then_fail)
    with pytest.raises(QuantStudioError, match="已被修改，已保留"):
        NotebookTemplateStore(tmp_path).instantiate(
            template["id"], template["revision"], "新研究", [], {}
        )
    assert {p["id"] for p in ProjectStore(tmp_path).list()} == {
        original["id"],
        changed[0]["id"],
    }
    assert ProjectStore(tmp_path).get(changed[0]["id"])["name"] == "另一次编辑"
