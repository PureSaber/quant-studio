"""Connect an operator-owned ETF snapshot and recipe without asking users for paths."""

import json
import threading
import uuid
from datetime import UTC, datetime

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.etf_daily import inspect_daily
from quant_studio.projects import ProjectStore, digest
from quant_studio.recipes import RecipeStore, _atomic, load_input_config

_LOCK = threading.RLock()


def connect(root, operation_id, *, project_id=None):
    from pathlib import Path

    value = inspect_daily()
    operation = value.get("latest") or {}
    if operation.get("id") != operation_id or not operation.get("research_recipe"):
        raise QuantStudioError("数据或预检已更新，请刷新今日运行后连接")
    path = Path(operation["research_recipe"])
    if file_hash(path) != operation["research_recipe_sha256"]:
        raise QuantStudioError("已冻结的ETF配方发生变化")
    inputs = load_input_config("etf-weekly-research", path)
    with _LOCK:
        index = Path(root) / ".etf-project-links.json"
        links = json.loads(index.read_text(encoding="utf-8")) if index.exists() else {}
        projects = ProjectStore(root)
        link_key = (
            operation_id if project_id is None else operation_id + ":" + project_id
        )
        if link_key in links:
            return projects.get(links[link_key])
        previous = projects.get(project_id) if project_id else None
        from quant_studio.notebook_data_reader import snapshot_hashes

        snapshot = Path(inputs["config"]["inputs"]["bundle"])
        hashes = snapshot_hashes(snapshot)
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        dataset = {
            "schema": "quant-studio.dataset/v1",
            "kind": "research-etf",
            "id": uuid.uuid4().hex,
            "name": "四ETF快照 · " + manifest["requested_end"],
            "template_id": "a-share-four-factor",
            "parent": None,
            "registered_at": datetime.now(UTC).isoformat(),
            "path": str(snapshot),
            "hashes": hashes,
            "identity": digest(hashes),
            "provider": manifest["source"]["provider"],
            "symbols": manifest["symbols"],
            "start": manifest["requested_start"],
            "end": manifest["requested_end"],
            "evidence": "QDK原生快照；历史主表及策略预检另行检查",
            "quality": operation["data"]["quality"],
            "limits": "当前采集的历史数据，不是未来留出",
        }
        _atomic(
            DatasetStore(root).root / (dataset["id"] + ".json"),
            json.dumps(dataset, ensure_ascii=False),
        )
        recipe = RecipeStore(root).save(
            "四ETF冻结周频基线",
            "etf-weekly-research",
            {},
            snapshot=str(path),
            input_config=inputs,
            note="仅连接新数据快照；策略、风险与费用保留冻结配方。",
            dataset_id=dataset["id"],
        )
        project = projects.save(
            previous["name"]
            if previous
            else "四ETF研究 · " + operation["data"]["covered_through"],
            previous["question"] if previous else inputs["config"]["hypothesis"],
            project_id=project_id,
            expected=previous["revision"] if previous else None,
            datasets=(previous["datasets"] if previous else []) + [dataset["id"]],
            recipes=(previous["recipes"] if previous else [])
            + [{"id": recipe["id"], "revision": recipe["revision"]}],
            notes=previous["notes"]
            if previous
            else "请登记研究卡，再预检运行。历史数据保持回顾性标签；负面结论保留。",
        )
        links[link_key] = project["id"]
        _atomic(index, json.dumps(links))
        return project
