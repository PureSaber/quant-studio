"""Server-owned native registration bridge using the existing persistent job queue."""

import json
import os
import subprocess
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.etf_experiments import read_family
from quant_studio.projects import ProjectStore, digest
from quant_studio.runtime import configured_python
from quant_studio.settings import setting, subprocess_environment

SCHEMA = "quant.project-registration/v1"


def configuration(root):
    raw = setting("QUANT_STUDIO_REGISTRATION")
    python = configured_python("quant-pipeline-registration")
    if not raw or not python:
        raise QuantStudioError(
            "尚未连接模拟登记配置和专用 quant-pipeline-registration 环境"
        )
    path = Path(raw)
    if not path.is_absolute() or not path.is_file():
        raise QuantStudioError("模拟登记配置必须是服务端已存在的绝对路径")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema") != SCHEMA
        or Path(value["runs_root"]).resolve() != Path(root).resolve()
    ):
        raise QuantStudioError("模拟登记配置未绑定当前研究工作区")
    return path, python


def argv(root, command, payload):
    path, python = configuration(root)
    return [
        python,
        "-I",
        "-B",
        "-X",
        "utf8",
        "-m",
        "quant_pipeline.project_registration",
        command,
        "--config",
        str(path),
        "--request-json",
        json.dumps(payload),
    ]


def _decode(result):
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise QuantStudioError("原生登记回执不可读；请回到预览页核验后恢复") from exc
    if result.returncode or value.get("schema") != SCHEMA:
        raise QuantStudioError(
            "原生登记检查未通过：" + str(value.get("error", result.stderr[-1000:]))
        )
    return value


def invoke(root, command, payload):
    try:
        result = subprocess.run(
            argv(root, command, payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env=subprocess_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return _decode(result)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise QuantStudioError(f"原生登记暂不可用：{exc}") from exc


def candidates(root, project_id):
    rows = []
    for record in ProjectStore(root).experiments(project_id):
        if record["template_id"] != "etf-weekly-research" or not record.get(
            "research_project"
        ):
            continue
        try:
            view = read_family(root, record["run_id"])
            if view["status"] != "succeeded" or not view.get("family_complete"):
                continue
            for row in view["results"]:
                if row["status"] == "completed":
                    rows.append(
                        {
                            "run_id": record["run_id"],
                            "candidate": row["candidate"],
                            "title": record["title"],
                        }
                    )
        except (QuantStudioError, OSError, ValueError):
            continue  # Failed/unverified experiments remain visible in project history.
    return rows


def prepare(root, project_id, revision, selected, start, end):
    project = ProjectStore(root).get(project_id)
    if project["revision"] != revision:
        raise QuantStudioError("项目已更新，请刷新后重新预览")
    choices = {digest(row): row for row in candidates(root, project_id)}
    if selected not in choices:
        raise QuantStudioError("请选择本项目已完成且通过核验的原生候选")
    row = choices[selected]
    return invoke(
        root,
        "preview",
        {
            "project_id": project_id,
            "revision": revision,
            "run_id": row["run_id"],
            "candidate": row["candidate"],
            "start": start,
            "end": end,
        },
    )


def inspect(root, project_id, preview_id):
    return invoke(root, "inspect", {"project_id": project_id, "preview_id": preview_id})


def queue_payload(root, project_id, preview_id):
    inspect(root, project_id, preview_id)
    path, _ = configuration(root)
    return {
        "collection_type": "simulation-registration",
        "project_id": project_id,
        "preview_id": preview_id,
        "config_sha256": file_hash(path),
    }


def execute_registration(root, job, control):
    from quant_studio.runner import _execute_subprocess

    payload = job["collection"]
    path, _ = configuration(root)
    if payload != {
        **queue_payload(root, payload["project_id"], payload["preview_id"]),
        "config_sha256": file_hash(path),
    }:
        raise QuantStudioError("登记配置已改变，请重新预览")
    control.stage("registration", "核验已确认预览，登记并检查模拟账户；调度独立")
    result = _execute_subprocess(
        argv(
            root,
            "commit",
            {"project_id": payload["project_id"], "preview_id": payload["preview_id"]},
        ),
        cwd=Path(root),
        env=subprocess_environment(),
        timeout=180,
        control=control,
    )
    value = _decode(result)
    return {
        "status": "succeeded" if value["state"] == "completed" else "failed",
        "message": "模拟账户登记已核验，尚未安排每日观察"
        if value["state"] == "completed"
        else "登记未完成，保留回执；返回预览页核验并恢复：" + str(value.get("error")),
        "result_url": (
            f"/projects/{payload['project_id']}/registration"
            f"?preview={payload['preview_id']}"
        ),
    }
