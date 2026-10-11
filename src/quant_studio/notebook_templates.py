"""Immutable personal Notebook templates and typed parameter bindings."""

import json
import math
import re
import shutil
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.notebooks import NotebookStore, _notebook
from quant_studio.projects import _LOCK, ProjectStore, digest
from quant_studio.recipes import _atomic

_ID = re.compile(r"[a-f0-9]{32}\Z")
_KEY = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,63}\Z")
PARAMETER_CELL = {
    "cell_type": "code",
    "id": "studio-parameters",
    "metadata": {},
    "execution_count": None,
    "outputs": [],
    "source": "import json\nfrom pathlib import Path\n"
    "context = json.loads(Path('inputs.json').read_text(encoding='utf-8'))\n"
    "parameters = context.get('parameters', {})\nparameters",
}


def _value(spec, value):
    kind = spec["type"]
    valid = {
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "string": isinstance(value, str) and len(value) <= 2000,
        "date": isinstance(value, str),
        "boolean": type(value) is bool,
        "choice": isinstance(value, str) and value in spec.get("choices", []),
    }.get(kind, False)
    if not valid:
        raise QuantStudioError(f"{spec['label']}：参数类型或选项无效")
    if kind in {"integer", "number"} and (
        not _finite(value)
        or any(
            bound in spec
            and (value < spec[bound] if bound == "min" else value > spec[bound])
            for bound in ("min", "max")
        )
    ):
        raise QuantStudioError(f"{spec['label']}：参数必须有限且在允许范围内")
    if kind == "date":
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError as exc:
            raise QuantStudioError("日期参数须为YYYY-MM-DD") from exc
    return value


def _finite(value):
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def parameter_schema(schema):
    if not isinstance(schema, dict) or len(schema) > 30:
        raise QuantStudioError("参数定义须为对象，最多30项")
    for key, spec in schema.items():
        if (
            not isinstance(key, str)
            or not _KEY.fullmatch(key)
            or not isinstance(spec, dict)
        ):
            raise QuantStudioError("参数名称须为英文标识符，定义须为对象")
        if not {"label", "type", "default"} <= set(spec) or set(spec) - {
            "label",
            "type",
            "default",
            "min",
            "max",
            "choices",
        }:
            raise QuantStudioError("参数定义需要label、type和default")
        if (
            not isinstance(spec["label"], str)
            or not 1 <= len(spec["label"].strip()) <= 120
        ):
            raise QuantStudioError("参数显示名称须为1至120字")
        if not isinstance(spec["type"], str) or spec["type"] not in {
            "integer",
            "number",
            "string",
            "date",
            "boolean",
            "choice",
        }:
            raise QuantStudioError("不支持的参数类型")
        for bound in ("min", "max"):
            if (
                bound in spec
                and spec["type"] == "integer"
                and type(spec[bound]) is not int
            ):
                raise QuantStudioError("整数参数的上下限必须为整数")
            if bound in spec and (
                spec["type"] not in {"integer", "number"}
                or type(spec[bound]) not in (int, float)
                or not _finite(spec[bound])
            ):
                raise QuantStudioError("参数上下限须为有限数值")
        if "min" in spec and "max" in spec and spec["min"] > spec["max"]:
            raise QuantStudioError("参数下限不可大于上限")
        if spec["type"] == "choice":
            choices = spec.get("choices")
            if (
                not isinstance(choices, list)
                or not 1 <= len(choices) <= 50
                or any(
                    not isinstance(v, str) or not 1 <= len(v) <= 200 for v in choices
                )
                or len(set(choices)) != len(choices)
            ):
                raise QuantStudioError("选项须为1至50个互不重复的短文本")
        elif "choices" in spec:
            raise QuantStudioError("仅choice参数可以定义choices")
        _value(spec, spec["default"])
    return schema


def parameter_values(schema, values, *, form=False):
    parameter_schema(schema)
    if not isinstance(values, dict) or set(values) - set(schema):
        raise QuantStudioError("存在未知参数")
    result = {}
    for key, spec in schema.items():
        value = values.get(key, spec["default"])
        if form and key in values:
            try:
                if spec["type"] == "integer":
                    value = int(value)
                elif spec["type"] == "number":
                    value = float(value)
                elif spec["type"] == "boolean":
                    if value not in {"true", "false"}:
                        raise ValueError()
                    value = value == "true"
            except (ValueError, TypeError, OverflowError) as exc:
                raise QuantStudioError(f"{spec['label']}：参数格式无效") from exc
        result[key] = _value(spec, value)
    return result


class NotebookTemplateStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / ".notebook-templates"
        if self.directory.resolve() != self.directory:
            raise QuantStudioError("模板目录不可为链接")

    def get(self, identifier):
        if not _ID.fullmatch(str(identifier)):
            raise QuantStudioError("模板标识无效")
        path = self.directory / f"{identifier}.json"
        try:
            if path.is_symlink() or path.stat().st_size > 3_000_000:
                raise QuantStudioError("模板文件无效")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise QuantStudioError("模板文档必须为对象")
            if value.get("id") != identifier or value.get("revision") != digest(
                {k: v for k, v in value.items() if k != "revision"}
            ):
                raise QuantStudioError("模板完整性核验失败")
            parameter_schema(value["parameters"])
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuantStudioError("模板不存在或无法读取") from exc

    def list(self):
        return sorted(
            [self.get(p.stem) for p in self.directory.glob("*.json")],
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def save(self, project_id, revision, name, parameters):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise QuantStudioError("模板名称须为1至120字")
        parameter_schema(parameters)
        project = ProjectStore(self.root).get(project_id, revision)
        store = NotebookStore(self.root)
        path = store.workspace(project_id) / "research.ipynb"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            raise QuantStudioError("请先保存2MB以内的Notebook草稿")
        notebook = _notebook(path.read_text(encoding="utf-8"))
        # Old generated notebooks embedded source dataset IDs; reusing these would
        # silently select the original data or an empty set in another project.
        sources = "\n".join(
            "".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"
        )
        if any(identifier in sources for identifier in project["datasets"]):
            raise QuantStudioError(
                "代码写死了数据集标识，请先改用context['datasets']读取所选数据后保存模板"
            )
        if not any(c.get("id") == "studio-parameters" for c in notebook["cells"]):
            notebook["cells"].insert(0, dict(PARAMETER_CELL))
        notebook = _notebook(json.dumps(notebook, ensure_ascii=False))
        value = {
            "schema": "quant-studio.notebook-template/v1",
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "question": project["question"],
            "parameters": parameters,
            "notebook": notebook,
            "source": {"project_id": project_id, "revision": revision},
            "created_at": datetime.now(UTC).isoformat(),
        }
        value["revision"] = digest(value)
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic(
            self.directory / f"{value['id']}.json",
            json.dumps(value, ensure_ascii=False),
        )
        return value

    def instantiate(self, identifier, revision, name, datasets, values):
        template = self.get(identifier)
        if template["revision"] != revision:
            raise QuantStudioError("模板版本不匹配，请刷新页面")
        values = parameter_values(template["parameters"], values)
        for container in (".projects", ".notebooks"):
            path = self.root / container
            if path.resolve() != path:
                raise QuantStudioError("新研究的存储目录不可为链接")
        # A failed creation is not a published project. Serialize project writes
        # while creating its draft, and only roll back this newly allocated ID.
        with _LOCK:
            projects = ProjectStore(self.root)
            project = projects.save(name, template["question"], datasets=datasets)
            owned = [self.root / c / project["id"] for c in (".notebooks", ".projects")]
            try:
                store = NotebookStore(self.root)
                workspace = store.workspace(project["id"])
                workspace.mkdir(parents=True)
                binding = {
                    "template": {
                        "id": identifier,
                        "revision": revision,
                        "name": template["name"],
                    },
                    "parameters": values,
                }
                _atomic(
                    workspace / "template.json", json.dumps(binding, ensure_ascii=False)
                )
                _atomic(
                    workspace / "research.ipynb",
                    json.dumps(template["notebook"], ensure_ascii=False),
                )
                store.initialize(project["id"], project["revision"])
            except Exception:
                # Refuse cleanup if another process has edited the new project or
                # redirected a directory; never delete outside the original root.
                if projects.get(project["id"])["revision"] != project["revision"]:
                    raise QuantStudioError(
                        "初始化失败，但新项目已被修改，已保留：" + project["id"]
                    ) from None
                for path in owned:
                    if path.resolve() != path or not path.is_relative_to(self.root):
                        raise QuantStudioError(
                            "初始化失败，新项目目录已改变，未执行清理"
                        ) from None
                for path in owned:
                    if path.exists():
                        shutil.rmtree(path)
                raise
            return project

    def binding(self, project_id):
        path = NotebookStore(self.root).workspace(project_id) / "template.json"
        if not path.exists():
            return {}
        try:
            if path.is_symlink() or path.stat().st_size > 100_000:
                raise QuantStudioError("模板参数文件无效")
            binding = json.loads(path.read_text(encoding="utf-8"))
            template = self.get(binding["template"]["id"])
            if binding["template"]["revision"] != template["revision"]:
                raise QuantStudioError("模板绑定版本不一致")
            return {
                "template": {k: template[k] for k in ("id", "revision", "name")},
                "parameters": parameter_values(
                    template["parameters"], binding["parameters"]
                ),
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuantStudioError("无法读取模板参数") from exc
