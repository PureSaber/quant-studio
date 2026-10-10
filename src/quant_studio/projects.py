"""Versioned research questions and explicit links to existing evidence."""

import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore
from quant_studio.recipes import RecipeStore, _atomic

_LOCK = threading.RLock()
_ID = re.compile(r"[a-f0-9]{32}\Z")
_REV = re.compile(r"[a-f0-9]{64}\Z")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


class ProjectStore:
    def __init__(self, runs_root):
        self.runs_root = Path(runs_root).resolve()
        self.root = self.runs_root / ".projects"
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, identifier, revision=None):
        if not _ID.fullmatch(str(identifier)) or (
            revision and not _REV.fullmatch(str(revision))
        ):
            raise QuantStudioError("研究项目标识无效")
        directory = self.root / identifier
        try:
            selected = revision or (directory / "HEAD").read_text().strip()
            if not _REV.fullmatch(selected):
                raise QuantStudioError("项目版本标识无效")
            value = json.loads(
                (directory / f"{selected}.json").read_text(encoding="utf-8")
            )
            content = {k: v for k, v in value.items() if k != "revision"}
            if (
                value.get("id") != identifier
                or value.get("revision") != selected
                or digest(content) != selected
            ):
                raise QuantStudioError("项目完整性核验失败")
            return value
        except (OSError, ValueError) as exc:
            raise QuantStudioError("研究项目不存在或无法读取") from exc

    def list(self, *, query=""):
        records = [
            self.get(p.name)
            for p in self.root.iterdir()
            if p.is_dir() and _ID.fullmatch(p.name)
        ]
        return sorted(
            [
                r
                for r in records
                if query.casefold()
                in (r["name"] + r["question"] + r["notes"]).casefold()
            ],
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def save(
        self,
        name,
        question,
        *,
        project_id=None,
        expected=None,
        datasets=None,
        recipes=None,
        notes="",
    ):
        for label, value, limit in (
            ("名称", name, 120),
            ("研究问题", question, 4000),
            ("研究笔记", notes, 16000),
        ):
            if (
                not isinstance(value, str)
                or len(value) > limit
                or (label != "研究笔记" and not value.strip())
            ):
                raise QuantStudioError(f"{label}须为非空文本，最多{limit}字")
        datasets, recipes = list(datasets or []), list(recipes or [])
        if len(datasets) > 100 or len(recipes) > 100:
            raise QuantStudioError("每个项目最多连接100个数据集和100个方案")
        if len(set(datasets)) != len(datasets):
            raise QuantStudioError("数据集不可重复连接")
        for identifier in datasets:
            DatasetStore(self.runs_root).get(identifier)
        for reference in recipes:
            if not isinstance(reference, dict) or set(reference) != {"id", "revision"}:
                raise QuantStudioError("方案须绑定标识及版本")
            RecipeStore(self.runs_root).get(reference["id"], reference["revision"])
        if len({(r["id"], r["revision"]) for r in recipes}) != len(recipes):
            raise QuantStudioError("方案版本不可重复连接")
        with _LOCK:
            previous = self.get(project_id) if project_id else None
            if previous and previous["revision"] != expected:
                raise QuantStudioError("项目已更新，请刷新后再保存")
            value = {
                "schema": "quant-studio.project/v1",
                "id": project_id or uuid.uuid4().hex,
                "name": name.strip(),
                "question": question.strip(),
                "notes": notes,
                "datasets": datasets,
                "recipes": recipes,
                "created_at": datetime.now(UTC).isoformat(),
                "parent": previous["revision"] if previous else None,
            }
            value["revision"] = digest(value)
            directory = self.root / value["id"]
            directory.mkdir(exist_ok=True)
            _atomic(
                directory / f"{value['revision']}.json",
                json.dumps(value, ensure_ascii=False),
            )
            _atomic(directory / "HEAD", value["revision"])
            return value

    def experiments(self, identifier):
        """Match exact linked recipe versions; never infer by name or latest HEAD."""
        from quant_studio.experiments import ExperimentStore

        selected = {
            (r["id"], r["revision"])
            for project in self.history(identifier)
            for r in project["recipes"]
        }
        return [
            r
            for r in ExperimentStore(self.runs_root).list()
            if (r["recipe"].get("id"), r["recipe"].get("revision")) in selected
        ]

    def history(self, identifier):
        current, records, seen = self.get(identifier), [], set()
        while current:
            if current["revision"] in seen:
                raise QuantStudioError("项目版本链存在循环")
            records.append(current)
            seen.add(current["revision"])
            current = (
                self.get(identifier, current["parent"]) if current["parent"] else None
            )
        return records
