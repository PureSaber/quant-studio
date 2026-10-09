from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from quant_studio import QuantStudioError


@dataclass(frozen=True)
class Template:
    metadata: dict[str, Any]
    base_config: dict[str, Any]
    directory: Any

    def __getattr__(self, name: str) -> Any:
        try:
            return self.metadata[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True)
class RenderedTemplate:
    config: dict[str, Any]
    values: dict[str, Any]


def template_ids() -> list[str]:
    root = files(__package__)
    builtin = [
        item.name
        for item in root.iterdir()
        if item.is_dir() and item.joinpath("template.json").is_file()
    ]
    custom = os.environ.get("QUANT_STUDIO_TEMPLATES")
    if custom:
        directory = Path(custom)
        if not directory.is_absolute() or not directory.is_dir():
            raise QuantStudioError("自定义模块目录须为已存在的绝对路径")
        builtin.extend(
            item.name
            for item in directory.iterdir()
            if item.is_dir()
            and item.name.startswith("custom-")
            and item.joinpath("template.json").is_file()
        )
    return sorted(builtin)


def load_template(template_id: str) -> Template:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", template_id):
        raise QuantStudioError("模板标识无效")
    directory = files(__package__).joinpath(template_id)
    custom = os.environ.get("QUANT_STUDIO_TEMPLATES")
    if template_id.startswith("custom-") and custom:
        root = Path(custom).resolve()
        directory = (root / template_id).resolve()
        if directory.parent != root:
            raise QuantStudioError("自定义模块目录越界")
    manifest = directory.joinpath("template.json")
    if not manifest.is_file():
        raise QuantStudioError(f"未知模板: {template_id}")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if template_id.startswith("custom-"):
        _validate_custom(metadata, template_id)
    base_path = directory.joinpath(metadata["base_config"])
    text = base_path.read_text(encoding="utf-8")
    if metadata["config_format"] == "yaml":
        base_config = yaml.safe_load(text)
    else:
        base_config = json.loads(text)
    return Template(metadata, base_config, directory)


def _validate_custom(metadata, template_id):
    required = {
        "id",
        "title",
        "summary",
        "kind",
        "workspace_repo",
        "upstream_ref",
        "base_config",
        "config_format",
        "knobs",
        "argv",
        "preflight_argv",
        "preflight_contract",
        "output_dirs",
        "result_files",
        "report_name",
        "disclaimer",
    }
    if not isinstance(metadata, dict) or required - set(metadata):
        raise QuantStudioError("自定义模块声明字段不完整")
    if (
        metadata["id"] != template_id
        or metadata["kind"] != "external"
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", metadata["workspace_repo"])
        or metadata["config_format"] not in {"json", "yaml"}
        or metadata["base_config"] not in {"base.json", "base.yaml"}
        or metadata["output_dirs"] != ["{output}"]
        or metadata["report_name"] != "report.html"
    ):
        raise QuantStudioError("自定义模块须使用独立输出目录和受支持的配置格式")
    for key in ("argv", "preflight_argv"):
        argv = metadata[key]
        if (
            not isinstance(argv, list)
            or len(argv) < 3
            or not all(isinstance(item, str) for item in argv)
            or argv[:2] != ["python", "-m"]
            or not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*", argv[2])
        ):
            raise QuantStudioError("自定义模块入口须为 python -m package.module")


def render_template(
    template: Template, supplied: dict[str, Any] | None = None
) -> RenderedTemplate:
    supplied = supplied or {}
    declared = {knob["name"]: knob for knob in template.knobs}
    for name in supplied:
        if name not in declared:
            raise QuantStudioError(f"未知参数 {name}")

    values: dict[str, Any] = {}
    for name, knob in declared.items():
        value = supplied.get(name, knob["default"])
        _validate_knob(name, value, knob)
        values[name] = value

    config = copy.deepcopy(template.base_config)
    for name, knob in declared.items():
        if knob.get("target", "config") == "config":
            _assign_path(config, knob["path"], values[name])
    return RenderedTemplate(config, values)


def apply_factor_selection(
    config: dict[str, Any], template: Template, names: list[str]
) -> None:
    catalog = list(template.metadata.get("factor_catalog") or [])
    if not catalog:
        raise QuantStudioError("该模板不能选择因子")
    known = {item["name"]: item for item in catalog}
    if not names:
        raise QuantStudioError("至少选择一个因子")
    selected: list[str] = []
    directions: dict[str, int] = {}
    for name in names:
        if name not in known:
            raise QuantStudioError(f"未知因子 {name}")
        if name in selected:
            continue
        selected.append(name)
        directions[name] = int(known[name]["direction"])
    config["factors"] = selected
    if "factor_directions" in config:
        config["factor_directions"] = directions


def _validate_knob(name: str, value: Any, knob: dict[str, Any]) -> None:
    kind = knob["type"]
    valid = True
    if kind == "enum":
        valid = isinstance(value, str) and value in knob["choices"]
    elif kind == "int":
        valid = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= knob["minimum"]
            and value <= knob["maximum"]
        )
        if "choices" in knob:
            valid = valid and value in knob["choices"]
    elif kind == "number":
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= knob["minimum"]
            and value <= knob["maximum"]
        )
    elif kind == "decimal-string":
        valid = (
            isinstance(value, str)
            and re.fullmatch(r"[0-9]+", value) is not None
            and knob["minimum_length"] <= len(value) <= knob["maximum_length"]
        )
    elif kind == "text":
        valid = (
            isinstance(value, str)
            and len(value) <= knob["max_length"]
            and re.fullmatch(knob["pattern"], value) is not None
        )
    elif kind == "date":
        try:
            valid = (
                isinstance(value, str)
                and date.fromisoformat(value).isoformat() == value
            )
        except ValueError:
            valid = False
    else:
        valid = False
    if not valid:
        raise QuantStudioError(f"参数 {name} 无效")


def _assign_path(config: dict[str, Any], path: str, value: Any) -> None:
    target = config
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


__all__ = [
    "RenderedTemplate",
    "Template",
    "load_template",
    "render_template",
    "template_ids",
    "apply_factor_selection",
]
