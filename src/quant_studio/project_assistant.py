"""Reviewable evidence packets for the separately installed quant-agent adapter."""

import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore, _atomic
from quant_studio.run_record import load_run_result
from quant_studio.runner import ExecutionCancelled, _execute_subprocess, safe_run_file
from quant_studio.runtime import configured_python
from quant_studio.settings import subprocess_environment


def _redact(text):
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"']+|(?<!\w)/[\w./-]+", "[本地路径]", text)
    return re.sub(
        r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=[已隐藏]", text
    )


class AssistantStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def directory(self, project_id, identifier):
        ProjectStore(self.root).get(project_id)
        if not re.fullmatch(r"[a-f0-9]{32}", str(identifier)):
            raise QuantStudioError("助手请求标识无效")
        directory = self.root / ".projects" / project_id / "advice" / identifier
        if directory.resolve() != directory:
            raise QuantStudioError("助手目录不可为链接")
        return directory

    def prepare(self, project_id, revision, question):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
            raise QuantStudioError("问题须为1至4000字")
        project = ProjectStore(self.root).get(project_id, revision)
        texts = [
            (
                "研究问题：" + project["question"],
                f"/projects/{project_id}?revision={revision}",
            )
        ]
        for identifier in project["datasets"][:10]:
            item = DatasetStore(self.root).get(identifier)
            evidence = {
                k: item[k]
                for k in (
                    "name",
                    "provider",
                    "start",
                    "end",
                    "symbols",
                    "identity",
                    "limits",
                )
            }
            texts.append(
                (
                    "已登记数据：" + json.dumps(evidence, ensure_ascii=False),
                    f"/catalog?dataset={identifier}",
                )
            )
        for ref in project["recipes"][:8]:
            recipe = RecipeStore(self.root).get(ref["id"], ref["revision"])
            texts.append(
                (
                    f"方案：{recipe['name']}；类型：{recipe['template_id']}；版本：{recipe['revision']}；说明：{recipe['note']}",
                    f"/research/{ref['id']}?revision={ref['revision']}",
                )
            )
        for run in ProjectStore(self.root).experiments(project_id)[:5]:
            outcome = load_run_result(safe_run_file(self.root, run["run_id"]))
            texts.append(
                (
                    f"实验：{run['title']}；状态：{run['status']}；"
                    f"诊断：{outcome.get('message', '')}；备注：{run['note']}",
                    f"/experiments/{run['run_id']}",
                )
            )
        for run in NotebookStore(self.root).list(project_id)[:5]:
            texts.append(
                (
                    f"Notebook实验：{run['id']}；状态：{run['status']}；"
                    f"诊断：{run.get('message', '')}",
                    f"/projects/{project_id}/notebook/{run['id']}",
                )
            )
        evidence, links = [], {}
        for i, (text, link) in enumerate(texts, 1):
            text = _redact(text).encode("utf-8")[:1200].decode("utf-8", errors="ignore")
            key = f"E{i}"
            evidence.append(
                {
                    "id": key,
                    "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
            links[key] = link
        request = {"question": _redact(question.strip()), "evidence": evidence}
        identifier = uuid.uuid4().hex
        directory = self.directory(project_id, identifier)
        directory.mkdir(parents=True)
        _atomic(
            directory / "context.json",
            json.dumps(request, ensure_ascii=False, indent=2),
        )
        record = {
            "id": identifier,
            "project_id": project_id,
            "project_revision": revision,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "prepared",
            "context_sha256": file_hash(directory / "context.json"),
            "links": links,
            "scope": "只包含页面列出的研究问题与元数据；"
            "不包含价格表、账户账本或Notebook代码。",
        }
        _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
        return record

    def get(self, project_id, identifier):
        directory = self.directory(project_id, identifier)
        try:
            record = json.loads(
                (directory / "request.json").read_text(encoding="utf-8")
            )
            if record["id"] != identifier or record["project_id"] != project_id:
                raise ValueError("助手记录标识无效")
            if file_hash(directory / "context.json") != record["context_sha256"]:
                raise QuantStudioError("待发送证据已变化，请重新准备")
            return record
        except (OSError, ValueError, KeyError) as exc:
            raise QuantStudioError("助手证据无法读取") from exc

    def list(self, project_id):
        ProjectStore(self.root).get(project_id)
        directory = self.root / ".projects" / project_id / "advice"
        return sorted(
            [
                self.get(project_id, p.name)
                for p in directory.glob("*")
                if p.is_dir() and re.fullmatch(r"[a-f0-9]{32}", p.name)
            ],
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def execute(self, payload, control):
        project_id, identifier = payload["project_id"], payload["id"]
        record = self.get(project_id, identifier)
        directory = self.directory(project_id, identifier)
        if record["status"] not in {"prepared", "queued"}:
            raise QuantStudioError("此证据请求已经处理，请新建请求")
        python = configured_python("quant-agent")
        if not python:
            raise QuantStudioError("请为quant-agent配置含工作台助手模块的独立环境")
        model = payload.get("model")
        if model and os.environ.get("QUANT_AGENT_LLM_OK") != "1":
            raise QuantStudioError("服务端尚未启用模型访问")
        argv = [
            python,
            "-m",
            "quant_agent.workbench_assistant",
            "--context",
            str(directory / "context.json"),
            "--output",
            str(directory / "answer.json"),
        ]
        if model:
            argv.extend(["--model", model])
        record.update(status="running", mode="model" if model else "evidence_only")
        _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
        try:
            completed = _execute_subprocess(
                argv,
                cwd=directory,
                env=subprocess_environment(),
                timeout=100,
                control=control,
            )
            if completed.returncode or not (directory / "answer.json").is_file():
                # Provider errors may include authorization details.
                raise QuantStudioError(
                    "助手未返回有效结果，请检查已安装模块及服务端模型配置"
                )
            self.get(project_id, identifier)
            record.update(
                status="succeeded", answer_sha256=file_hash(directory / "answer.json")
            )
        except ExecutionCancelled:
            record.update(status="cancelled", message="助手任务已取消")
        except (OSError, subprocess.SubprocessError, QuantStudioError) as exc:
            record.update(status="failed", message=str(exc))
        _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
        return {
            "status": record["status"],
            "message": record.get("message", "研究建议已保存"),
        }
