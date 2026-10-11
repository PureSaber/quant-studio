"""Search verified Studio project versions, Notebook conditions, results and errors."""

import json
import re

from quant_studio.notebook_metrics import load_metrics
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore, digest


def search_history(root, query="", *, limit=20):
    if not isinstance(query, str) or len(query) > 4000 or not 1 <= limit <= 50:
        raise ValueError("检索问题或结果数量无效")
    projects, notebooks = ProjectStore(root), NotebookStore(root)
    rows = []
    for project in projects.list():
        identifier = project["id"]
        for version in projects.history(identifier):
            rows.append(
                {
                    "project_id": identifier,
                    "kind": "项目版本",
                    "title": version["name"],
                    "status": "saved",
                    "created_at": version["created_at"],
                    "text": version["question"] + "\n" + version["notes"],
                    "link": f"/projects/{identifier}?revision={version['revision']}",
                    "identity": version["revision"],
                }
            )
        for run in notebooks.list(identifier):
            original_project = projects.get(identifier, run["project_revision"])
            text = (
                f"{original_project['name']}；{original_project['question']}；"
                f"{run['status']}；{run.get('message', '')}"
            )
            for filename in ("inputs.json", "metrics.json", "execution.log"):
                if filename not in {**run["hashes"], **run.get("artifacts", {})}:
                    continue
                path = notebooks.artifact(identifier, run["id"], filename)
                if filename == "inputs.json":
                    context = json.loads(path.read_text("utf-8"))
                    value = {
                        "parameters": context.get("parameters", {}),
                        "datasets": [
                            {k: d.get(k) for k in ("id", "name", "identity", "scope")}
                            for d in context["datasets"]
                        ],
                    }
                    text += "\n条件：" + json.dumps(value, ensure_ascii=False)
                elif filename == "metrics.json" and run["status"] == "succeeded":
                    text += "\n指标：" + json.dumps(
                        load_metrics(path), ensure_ascii=False
                    )
                elif filename == "execution.log":
                    text += "\n日志尾部：" + path.read_text("utf-8")[-6000:]
            rows.append(
                {
                    "project_id": identifier,
                    "kind": "Notebook实验",
                    "title": original_project["name"],
                    "status": run["status"],
                    "created_at": run["created_at"],
                    "text": text,
                    "link": f"/projects/{identifier}/notebook/{run['id']}",
                    "identity": digest(run),
                }
            )
        for run in projects.experiments(identifier):
            rows.append(
                {
                    "project_id": identifier,
                    "kind": "方案实验",
                    "title": run["title"],
                    "status": run["status"],
                    "created_at": run.get("created_at", ""),
                    "text": json.dumps(run, ensure_ascii=False),
                    "link": f"/experiments/{run['run_id']}",
                    "identity": digest(run),
                }
            )
    tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", query.casefold()))
    for row in rows:
        words = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", row["text"].casefold()))
        row["score"] = len(tokens & words) if tokens else 1
    return sorted(
        [r for r in rows if r["score"]],
        key=lambda r: (r["score"], r["created_at"]),
        reverse=True,
    )[:limit]
