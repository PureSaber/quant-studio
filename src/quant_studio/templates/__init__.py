from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from importlib.resources import files
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
    return sorted(
        item.name
        for item in root.iterdir()
        if item.is_dir() and item.joinpath("template.json").is_file()
    )


def load_template(template_id: str) -> Template:
    directory = files(__package__).joinpath(template_id)
    manifest = directory.joinpath("template.json")
    if not manifest.is_file():
        raise QuantStudioError(f"未知模板: {template_id}")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    base_path = directory.joinpath(metadata["base_config"])
    text = base_path.read_text(encoding="utf-8")
    if metadata["config_format"] == "yaml":
        base_config = yaml.safe_load(text)
    else:
        base_config = json.loads(text)
    return Template(metadata, base_config, directory)


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
