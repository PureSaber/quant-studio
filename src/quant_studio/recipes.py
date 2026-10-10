"""Versioned, server-owned research plans; never accept executable template metadata."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from quant_studio import QuantStudioError
from quant_studio.templates import Template, load_template, render_template

SCHEMA = "quant-studio.recipe/v1"
MAX_BYTES = 48_000
_ID = re.compile(r"[a-f0-9]{32}\Z")
_REV = re.compile(r"[a-f0-9]{64}\Z")
_INPUT_PATHS = {
    "stat-arb-research": (
        "input.path",
        "input.total_return",
        "input.industry",
        "input.adv",
    ),
    "timing-research": (
        "input.path",
        "signals.path",
        "activity.path",
        "regime.history",
        "regime.snapshot",
        "macro.context",
        "macro.history",
        "futures.path",
    ),
}
_INPUT_PATHS["timing-counterfactual"] = _INPUT_PATHS["timing-research"]


def load_input_config(template_id, path):
    if template_id not in _INPUT_PATHS:
        raise QuantStudioError("此模块未声明外部配置编辑契约")
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise QuantStudioError("请选择不超过 48 KB 的已有研究配置文件")
    raw = path.read_bytes()
    value = {
        "config": parse_config(raw.decode("utf-8-sig")),
        "source_path": str(path),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return _normalize_input(template_id, value)


def _normalize_input(template_id, value):
    if value is None:
        return None
    if (
        template_id not in _INPUT_PATHS
        or not isinstance(value, dict)
        or set(value) != {"config", "source_path", "source_sha256"}
        or not isinstance(value["config"], dict)
        or not isinstance(value["source_path"], str)
        or not Path(value["source_path"]).is_absolute()
        or not _REV.fullmatch(str(value["source_sha256"]))
    ):
        raise QuantStudioError("外部研究配置格式无效")
    _check_tree(value)
    result = copy.deepcopy(value)
    base = Path(value["source_path"]).parent
    for dotted in _INPUT_PATHS[template_id]:
        section, key = dotted.split(".")
        mapping = result["config"].get(section)
        if not isinstance(mapping, dict) or mapping.get(key) is None:
            continue
        path = mapping[key]
        if not isinstance(path, str) or not path.strip():
            raise QuantStudioError(f"{dotted} 须为输入文件路径")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = (base / path).resolve()
            if template_id.startswith("timing-") and not candidate.is_file():
                alternative = (base.parent / path).resolve()
                if alternative.is_file():
                    candidate = alternative
        mapping[key] = str(candidate)
    return result


class _ConfigLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise QuantStudioError("配置键须为不重复的文本")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_ConfigLoader.add_constructor("tag:yaml.org,2002:map", _unique_mapping)
# Keep ISO dates as native configuration strings, not datetime objects.
_ConfigLoader.add_constructor(
    "tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node)
)


def _check_tree(value, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 24 or budget[0] > 4000:
        raise QuantStudioError("配置过深、过大或存在循环引用")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise QuantStudioError("配置键须为文本")
            _check_tree(child, depth + 1, budget)
    elif isinstance(value, list):
        for child in value:
            _check_tree(child, depth + 1, budget)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise QuantStudioError("配置不接受非有限数值")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise QuantStudioError("配置须为 JSON 兼容的数据")


def parse_config(text: str) -> dict:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise QuantStudioError("配置超过 48 KB")
    try:
        value = yaml.load(text, Loader=_ConfigLoader)
        _check_tree(value)
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        raise QuantStudioError("配置格式错误") from exc
    if not isinstance(value, dict) or not value:
        raise QuantStudioError("配置须为非空对象")
    return value


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def _merge_defaults(defaults, supplied):
    result = copy.deepcopy(defaults)
    for key, value in supplied.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate(record: dict) -> Template:
    required = {
        "schema_version",
        "id",
        "revision",
        "name",
        "template_id",
        "template_digest",
        "config",
        "cli",
        "snapshot",
        "created_at",
        "parent",
    }
    if (
        not isinstance(record, dict)
        or required - set(record)
        or set(record) - required - {"input_config", "note", "dataset_id"}
    ):
        raise QuantStudioError("研究方案格式错误")
    _check_tree(record)
    if record.get("dataset_id") is not None and not _ID.fullmatch(
        str(record["dataset_id"])
    ):
        raise QuantStudioError("数据集标识无效")
    if (
        not isinstance(record.get("note", ""), str)
        or len(record.get("note", "")) > 2000
    ):
        raise QuantStudioError("版本说明须为不超过 2000 字的文本")
    if record["schema_version"] != SCHEMA or not _ID.fullmatch(str(record["id"])):
        raise QuantStudioError("研究方案标识无效")
    if (
        not _REV.fullmatch(str(record["revision"]))
        or _digest({k: v for k, v in record.items() if k != "revision"})
        != record["revision"]
    ):
        raise QuantStudioError("研究方案校验失败；请导入原生配置以创建新版本")
    if (
        not isinstance(record["name"], str)
        or not 1 <= len(record["name"].strip()) <= 120
    ):
        raise QuantStudioError("方案名称须为 1–120 个字符")
    if not isinstance(record["template_id"], str):
        raise QuantStudioError("模板标识须为文本")
    template = load_template(record["template_id"])
    _normalize_input(record["template_id"], record.get("input_config"))
    if record["template_digest"] != _digest(template.metadata):
        raise QuantStudioError("模板已变更；请将配置导入当前模板重新预检")
    if not isinstance(record["config"], dict) or not isinstance(record["cli"], dict):
        raise QuantStudioError("配置与运行参数须为对象")
    unknown = set(record["config"]) - set(template.base_config)
    if unknown:
        raise QuantStudioError(f"未知原生配置字段：{', '.join(sorted(unknown))}")
    if record["snapshot"] is not None and (
        not isinstance(record["snapshot"], str)
        or not Path(record["snapshot"]).is_absolute()
    ):
        raise QuantStudioError("输入路径须为服务端绝对路径")
    metadata = copy.deepcopy(template.metadata)
    supplied = record["cli"]
    allowed_cli = {k["name"] for k in template.knobs if k.get("target") == "cli"}
    if set(supplied) - allowed_cli:
        raise QuantStudioError("未知运行参数")
    try:
        for knob in metadata["knobs"]:
            if knob.get("target") == "cli":
                knob["default"] = supplied.get(knob["name"], knob["default"])
            else:
                value = record["config"]
                for part in knob["path"].split("."):
                    value = value[part]
                knob["default"] = value
        loaded = Template(metadata, copy.deepcopy(record["config"]), template.directory)
        render_template(loaded)
    except (KeyError, TypeError) as exc:
        raise QuantStudioError("配置缺少模板要求的字段或字段类型错误") from exc
    return loaded


def recipe_template(record: dict) -> Template:
    template = _validate(record)
    if template.id == "a-share-four-factor":
        quantiles = template.base_config.get("quantiles", 5)
        if type(quantiles) is not int or quantiles < 1:
            raise QuantStudioError("分组数量须为正整数")
        template.metadata["nav_column"] = f"Q{quantiles}"
    template.metadata["recipe"] = copy.deepcopy(record)
    return template


class RecipeStore:
    def __init__(self, runs_root: str | Path):
        self.root = Path(runs_root).resolve() / ".recipes"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def get(self, recipe_id: str, revision: str | None = None) -> dict:
        if not _ID.fullmatch(recipe_id) or (revision and not _REV.fullmatch(revision)):
            raise QuantStudioError("研究方案标识无效")
        with self._lock:
            try:
                directory = self.root / recipe_id
                selected = revision or (directory / "HEAD").read_text().strip()
                if not _REV.fullmatch(selected):
                    raise QuantStudioError("研究方案索引损坏")
                value = json.loads(
                    (directory / f"{selected}.json").read_text(encoding="utf-8")
                )
                _validate(value)
                if value["id"] != recipe_id or value["revision"] != selected:
                    raise QuantStudioError("研究方案索引校验失败")
                return value
            except (OSError, ValueError) as exc:
                raise QuantStudioError("研究方案不存在或损坏") from exc

    def list(self) -> list[dict]:
        return sorted(
            [
                self.get(path.name)
                for path in self.root.iterdir()
                if path.is_dir() and _ID.fullmatch(path.name)
            ],
            key=lambda value: value["created_at"],
            reverse=True,
        )

    def history(self, recipe_id: str) -> list[dict]:
        """Follow the committed parent chain, excluding unpublished partial saves."""
        rows, seen = [], set()
        current = self.get(recipe_id)
        while current:
            revision = current["revision"]
            if revision in seen or len(rows) >= 10000:
                raise QuantStudioError("版本历史存在循环或过长")
            seen.add(revision)
            rows.append(current)
            current = (
                self.get(recipe_id, current["parent"]) if current["parent"] else None
            )
        return list(reversed(rows))

    def save(
        self,
        name,
        template_id,
        config,
        *,
        cli=None,
        snapshot=None,
        recipe_id=None,
        expected=None,
        input_config=None,
        note="",
        dataset_id=None,
    ) -> dict:
        with self._lock:
            previous = self.get(recipe_id) if recipe_id else None
            if previous and previous["revision"] != expected:
                raise QuantStudioError(
                    "方案已更新，请刷新页面后再编辑，避免覆盖其他设备的修改"
                )
            template = load_template(template_id)
            if not isinstance(config, dict):
                raise QuantStudioError("配置须为对象")
            _check_tree(config)
            record = {
                "schema_version": SCHEMA,
                "id": recipe_id or uuid.uuid4().hex,
                "name": name,
                "template_id": template_id,
                "template_digest": _digest(template.metadata),
                "config": _merge_defaults(render_template(template).config, config),
                "cli": copy.deepcopy(cli or {}),
                "snapshot": snapshot or None,
                "created_at": datetime.now(UTC).isoformat(),
                "parent": previous["revision"] if previous else None,
                "input_config": _normalize_input(template_id, input_config),
                "note": note,
                "dataset_id": dataset_id,
            }
            _check_tree(record)
            record["revision"] = _digest(record)
            _validate(record)
            text = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)
            if len(text.encode("utf-8")) > MAX_BYTES:
                raise QuantStudioError("研究方案超过 48 KB")
            directory = self.root / record["id"]
            directory.mkdir(exist_ok=True)
            _atomic(directory / f"{record['revision']}.json", text)
            _atomic(directory / "HEAD", record["revision"])
            return record

    def import_document(self, text: str, *, name=None) -> dict:
        record = parse_config(text)
        _validate(record)
        return self.save(
            name or record["name"],
            record["template_id"],
            record["config"],
            cli=record["cli"],
            snapshot=record["snapshot"],
            input_config=record.get("input_config"),
            note=record.get("note", ""),
        )


def _atomic(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
