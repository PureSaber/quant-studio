"""Frozen Notebook parameter grids dispatched through the existing FIFO queue."""

import itertools
import json
import uuid
from datetime import UTC, datetime

from quant_studio import QuantStudioError
from quant_studio.notebook_templates import NotebookTemplateStore, parameter_values
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import _LOCK, digest
from quant_studio.recipes import _atomic


class NotebookBatchStore:
    def __init__(self, root):
        self.notebooks = NotebookStore(root)

    def _path(self, project, identifier):
        # Reuse the execution ID/path validator but store batch metadata separately.
        directory = (
            self.notebooks.directory(project, identifier).parent.parent
            / "notebook-batches"
        )
        if directory.resolve() != directory:
            raise QuantStudioError("批次目录不可为链接")
        return directory / f"{identifier}.json"

    def get(self, project, identifier):
        try:
            path = self._path(project, identifier)
            if path.is_symlink():
                raise QuantStudioError("批次文件不可为链接")
            value = json.loads(path.read_text("utf-8"))
            if (
                value["id"] != identifier
                or value["project_id"] != project
                or value["revision"]
                != digest({k: v for k, v in value.items() if k != "revision"})
            ):
                raise QuantStudioError("批次完整性失败")
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuantStudioError("批次不存在或损坏") from exc

    def _save(self, value):
        value["revision"] = digest({k: v for k, v in value.items() if k != "revision"})
        path = self._path(value["project_id"], value["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic(path, json.dumps(value, ensure_ascii=False, indent=2))

    def list(self, project):
        directory = self._path(project, "0" * 32).parent
        return sorted(
            [self.get(project, p.stem) for p in directory.glob("*.json")],
            key=lambda b: b["created_at"],
            reverse=True,
        )

    def prepare(self, project, revision, template_id, grid, *, max_candidates):
        template = NotebookTemplateStore(self.notebooks.root).get(template_id)
        schema = template["parameters"]
        if type(max_candidates) is not int or not 1 <= max_candidates <= 32:
            raise QuantStudioError("单批候选上限须在1至32之间")
        if not isinstance(grid, dict) or set(grid) - set(schema):
            raise QuantStudioError("参数网格含未知字段")
        count = 1
        for key, values in grid.items():
            if not isinstance(values, list) or not values:
                raise QuantStudioError("每个参数至少需要一个候选值")
            count *= len(values)
            if count > max_candidates:
                raise QuantStudioError("候选数超过上限，请缩小参数网格")
            for value in values:
                parameter_values(schema, {key: value})
        candidates = [
            parameter_values(schema, dict(zip(grid, row, strict=True)))
            for row in itertools.product(*grid.values())
        ]
        base = self.notebooks.prepare(
            project, revision, parameters=candidates[0], parameter_template=template_id
        )
        batch = {
            "id": uuid.uuid4().hex,
            "project_id": project,
            "project_revision": revision,
            "created_at": datetime.now(UTC).isoformat(),
            "grid": grid,
            "template": template_id,
            "candidates": [],
            "status": "preparing",
        }
        self._save(batch)
        try:
            for index, values in enumerate(candidates):
                run = (
                    base
                    if index == 0
                    else self.notebooks.fork(project, base["id"], parameters=values)
                )
                batch["candidates"].append(
                    {"parameters": values, "attempts": [run["id"]]}
                )
                self._save(batch)
            batch["status"] = "prepared"
        except Exception:
            batch["status"] = "blocked"
            self._save(batch)
            raise
        self._save(batch)
        return batch

    def _enqueue(self, project, execution, manager):
        return manager.submit(
            "notebook",
            "notebook",
            {},
            profile=None,
            notebook={"project_id": project, "id": execution},
            dispatch_key=digest(["notebook", project, execution]),
        )

    def submit(self, project, identifier, manager):
        with _LOCK:
            batch = self.get(project, identifier)
            if batch["status"] not in {"prepared", "submitted"}:
                raise QuantStudioError("批次未准备完成或已取消")
            # Persist intent first; queue keys make partial submission resumable.
            batch["status"] = "submitted"
            self._save(batch)
            for candidate in batch["candidates"]:
                execution = candidate["attempts"][-1]
                record = self.notebooks.get(project, execution)
                if record["status"] == "ready" or record.get("job_id"):
                    self._enqueue(project, execution, manager)
            return batch

    def retry(self, project, identifier, execution, manager):
        with _LOCK:
            batch = self.get(project, identifier)
            candidate = next(
                (c for c in batch["candidates"] if c["attempts"][-1] == execution), None
            )
            if candidate is None or self.notebooks.get(project, execution)[
                "status"
            ] not in {"failed", "cancelled", "interrupted", "blocked"}:
                raise QuantStudioError("只能重试当前失败、中断或取消的实验")
            record = self.notebooks.fork(project, execution)
            candidate["attempts"].append(record["id"])
            batch["status"] = "submitted"
            self._save(batch)
            self._enqueue(project, record["id"], manager)
            return batch

    def cancel(self, project, identifier, manager):
        with _LOCK:
            batch = self.get(project, identifier)
            batch["status"] = "cancelled"
            self._save(batch)
            for candidate in batch["candidates"]:
                record = self.notebooks.get(project, candidate["attempts"][-1])
                if record.get("job_id") and record["status"] in {
                    "queued",
                    "running",
                    "cancelling",
                }:
                    manager.cancel(record["job_id"])
                elif record["status"] == "ready":
                    record.update(status="cancelled", message="批次已取消")
                    self.notebooks._save(
                        self.notebooks.directory(project, record["id"]), record
                    )
            return batch
