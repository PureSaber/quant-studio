"""Optional Jupyter workspace and immutable, queued notebook experiments."""

import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from quant_studio import QuantStudioError
from quant_studio.data_catalog import registered_file
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.projects import ProjectStore
from quant_studio.recipes import _atomic
from quant_studio.runner import ExecutionCancelled, _execute_subprocess

_ID = re.compile(r"[a-f0-9]{32}\Z")
ARTIFACTS = {"report.html", "executed.ipynb", "execution.log", "environment.json"}


def notebook_config():
    source = os.environ.get("QUANT_STUDIO_NOTEBOOK_CONFIG")
    if not source:
        raise QuantStudioError(
            "尚未配置独立Notebook环境，请按研究工作区指南连接JupyterLab"
        )
    try:
        value = json.loads(Path(source).read_text(encoding="utf-8-sig"))
        python = Path(value["python"])
        parsed = urlsplit(value["lab_url"])
        if (
            not python.is_absolute()
            or not python.is_file()
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
            or parsed.path not in {"", "/"}
            or (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "localhost"}
                )
            )
        ):
            raise ValueError("Python路径或Lab地址无效")
        timeout = value.get("timeout_seconds", 600)
        max_bytes = value.get("max_input_bytes", 1024**3)
        if type(timeout) is not int or not 10 <= timeout <= 86400:
            raise ValueError("超时须在10至86400秒之间")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 100 * 1024**3:
            raise ValueError("输入复制上限无效")
        return {
            "python": str(python),
            "lab_url": value["lab_url"].rstrip("/"),
            "timeout_seconds": timeout,
            "max_input_bytes": max_bytes,
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QuantStudioError("Notebook配置无效：" + str(exc)) from exc


def _notebook(text):
    if len(text.encode("utf-8")) > 2_000_000:
        raise QuantStudioError("Notebook源码超过2MB，请清理输出后重试")
    try:
        value = json.loads(text)
        if value.get("nbformat") != 4 or not isinstance(value.get("cells"), list):
            raise ValueError("需要Notebook v4")
        if not 1 <= len(value["cells"]) <= 200:
            raise ValueError("单次实验须有1至200个单元格")
        for cell in value["cells"]:
            if cell["cell_type"] not in {"code", "markdown", "raw"}:
                raise ValueError("不支持的单元格类型")
            source = cell["source"]
            if not isinstance(source, str) and not (
                isinstance(source, list) and all(isinstance(v, str) for v in source)
            ):
                raise ValueError("单元格源码无效")
            if cell["cell_type"] == "code":
                cell["outputs"], cell["execution_count"] = [], None
            # Execution always uses the selected Python, never notebook kernel metadata.
            cell["metadata"] = {}
        value["metadata"] = {
            "kernelspec": {
                "display_name": "研究环境",
                "language": "python",
                "name": "python3",
            }
        }
        return value
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise QuantStudioError("Notebook源码无效：" + str(exc)) from exc


class NotebookStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def workspace(self, identifier):
        ProjectStore(self.root).get(identifier)
        directory = self.root / ".notebooks" / identifier
        if directory.exists() and directory.resolve() != directory:
            raise QuantStudioError("Notebook工作区不可为链接")
        return directory

    def directory(self, identifier, execution):
        if not _ID.fullmatch(str(execution)):
            raise QuantStudioError("实验标识无效")
        ProjectStore(self.root).get(identifier)
        directory = self.root / ".projects" / identifier / "executions" / execution
        if directory.exists() and directory.resolve() != directory:
            raise QuantStudioError("实验目录不可为链接")
        return directory

    def initialize(self, identifier, revision=None):
        project = ProjectStore(self.root).get(identifier, revision)
        directory = self.workspace(identifier)
        directory.mkdir(parents=True, exist_ok=True)
        draft = directory / "research.ipynb"
        # Refresh the input map explicitly without overwriting the user's code.
        context = self._context(project)
        _atomic(directory / "inputs.json", json.dumps(context, ensure_ascii=False))
        if draft.exists():
            return draft
        cells = [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": project["question"],
                "id": "question",
            },
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "id": "inputs",
                "source": "import json\nfrom pathlib import Path\n"
                "context = json.loads(\n"
                '    Path("inputs.json").read_text(encoding="utf-8"))\n'
                "context",
            },
            {
                "cell_type": "code",
                "metadata": {},
                "execution_count": None,
                "outputs": [],
                "id": "explore",
                "source": "# 在这里编写数据探索、统计检验或绘图。\n"
                '# 数据路径来自context["datasets"]，提交实验会复制已登记输入。\n'
                'print("研究问题：", context["question"])',
            },
        ]
        _atomic(directory / "inputs.json", json.dumps(context, ensure_ascii=False))
        # Exclusive creation avoids overwriting a concurrent browser editor.
        with draft.open("x", encoding="utf-8") as stream:
            json.dump(
                {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells},
                stream,
                ensure_ascii=False,
            )
        return draft

    def _context(self, project):
        return {
            "question": project["question"],
            "project_revision": project["revision"],
            "datasets": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "path": str(Path(item["path"]).parent)
                    if "." in item["hashes"]
                    else item["path"],
                    "identity": item["identity"],
                    "files": [
                        Path(item["path"]).name if n == "." else n
                        for n in item["hashes"]
                    ],
                }
                for item in (
                    DatasetStore(self.root).get(identifier)
                    for identifier in project["datasets"]
                )
            ],
        }

    def lab_link(self, identifier):
        self.workspace(identifier)
        return notebook_config()["lab_url"] + f"/lab/tree/{identifier}/research.ipynb"

    def prepare(self, identifier, revision):
        config = notebook_config()
        project = ProjectStore(self.root).get(identifier, revision)
        draft = self.workspace(identifier) / "research.ipynb"
        if (
            not draft.is_file()
            or draft.is_symlink()
            or draft.stat().st_size > 2_000_000
        ):
            raise QuantStudioError("请先创建并保存2MB以内的Notebook草稿")
        source = _notebook(draft.read_text(encoding="utf-8"))
        execution = uuid.uuid4().hex
        directory = self.directory(identifier, execution)
        directory.mkdir(parents=True)
        record = {
            "schema": "quant-studio.notebook-run/v1",
            "id": execution,
            "project_id": identifier,
            "project_revision": revision,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "preparing",
            "runtime": config,
            "hashes": {},
        }
        self._save(directory, record)
        try:
            _atomic(directory / "source.ipynb", json.dumps(source, ensure_ascii=False))
            worker = Path(__file__).with_name("notebook_worker.py")
            shutil.copyfile(worker, directory / "executor.py")
            try:
                inspected = subprocess.run(
                    [
                        config["python"],
                        "-I",
                        "-X",
                        "utf8",
                        str(worker),
                        "--environment",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                    check=True,
                )
                environment = json.loads(inspected.stdout)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                raise QuantStudioError("无法记录Notebook环境身份") from exc
            _atomic(directory / "requested-environment.json", json.dumps(environment))
            context = self._context(project)
            total = 0
            for item in context["datasets"]:
                registered = DatasetStore(self.root).get(item["id"])
                target = directory / "inputs" / item["id"]
                target.mkdir(parents=True)
                for name in registered["hashes"]:
                    _, original = registered_file(self.root, item["id"], name)
                    total += original.stat().st_size
                    if total > config["max_input_bytes"]:
                        raise QuantStudioError("输入超过配置的复制上限，请缩小数据集")
                    relative = original.name if name == "." else name
                    copied = target / relative
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, copied)
                    if file_hash(copied) != registered["hashes"][name]:
                        raise QuantStudioError("复制期间输入变化")
                    record["hashes"][copied.relative_to(directory).as_posix()] = (
                        file_hash(copied)
                    )
                item["path"] = target.relative_to(directory).as_posix()
                item["files"] = [
                    Path(registered["path"]).name if n == "." else n
                    for n in item["files"]
                ]
            _atomic(directory / "inputs.json", json.dumps(context, ensure_ascii=False))
            for name in (
                "source.ipynb",
                "inputs.json",
                "executor.py",
                "requested-environment.json",
            ):
                record["hashes"][name] = file_hash(directory / name)
            record["status"] = "ready"
        except Exception as exc:
            record.update(status="blocked", message=str(exc))
            self._save(directory, record)
            raise
        self._save(directory, record)
        return record

    @staticmethod
    def _save(directory, record):
        _atomic(
            directory / "run.json", json.dumps(record, ensure_ascii=False, indent=2)
        )

    def get(self, identifier, execution):
        try:
            record = json.loads(
                (self.directory(identifier, execution) / "run.json").read_text(
                    encoding="utf-8"
                )
            )
            if (
                record["id"] != execution
                or record["project_id"] != identifier
                or record["schema"] != "quant-studio.notebook-run/v1"
                or not set(record.get("artifacts", {})) <= ARTIFACTS
            ):
                raise ValueError("实验记录标识或产物无效")
            ProjectStore(self.root).get(identifier, record["project_revision"])
            return record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuantStudioError("Notebook实验记录无法读取") from exc

    def artifact(self, identifier, execution, name):
        record = self.get(identifier, execution)
        expected = {**record["hashes"], **record.get("artifacts", {})}
        if (
            name not in ARTIFACTS | {"source.ipynb", "inputs.json"}
            or name not in expected
        ):
            raise QuantStudioError("实验产物不可下载")
        path = self.directory(identifier, execution) / name
        if path.is_symlink() or not path.is_file() or file_hash(path) != expected[name]:
            raise QuantStudioError("实验产物完整性失败")
        return path

    def list(self, identifier):
        ProjectStore(self.root).get(identifier)
        directory = self.root / ".projects" / identifier / "executions"
        return sorted(
            [
                self.get(identifier, p.name)
                for p in directory.glob("*")
                if p.is_dir() and _ID.fullmatch(p.name)
            ],
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def execute(self, payload, control):
        identifier, execution = payload["project_id"], payload["id"]
        record = self.get(identifier, execution)
        directory = self.directory(identifier, execution)
        if record["status"] not in {"ready", "queued"}:
            raise QuantStudioError("此实验已提交或准备失败，请创建新实验")
        record["status"] = "running"
        self._save(directory, record)
        runtime = record["runtime"]
        # The executor receives no model/API credentials from the Studio process.
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "PATHEXT",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "PROGRAMDATA",
                "COMSPEC",
            }
        }
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            self.verify_inputs(record)
            result = _execute_subprocess(
                [
                    runtime["python"],
                    "-I",
                    "-X",
                    "utf8",
                    str(directory / "executor.py"),
                    str(directory),
                    str(runtime["timeout_seconds"]),
                ],
                cwd=directory,
                env=env,
                timeout=runtime["timeout_seconds"] + 90,
                control=control,
            )
            (directory / "execution.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            self.verify_inputs(record)
            record.update(
                status="succeeded" if result.returncode == 0 else "failed",
                message="Notebook执行完成"
                if result.returncode == 0
                else "Notebook执行失败，请查看实验日志",
            )
        except ExecutionCancelled:
            record.update(status="cancelled", message="Notebook实验已取消")
        except (OSError, subprocess.SubprocessError, QuantStudioError) as exc:
            record.update(status="failed", message=str(exc))
        record["artifacts"] = {
            name: file_hash(directory / name)
            for name in (
                "report.html",
                "executed.ipynb",
                "execution.log",
                "environment.json",
            )
            if (directory / name).is_file() and not (directory / name).is_symlink()
        }
        self._save(directory, record)
        return {"status": record["status"], "message": record.get("message", "")}

    def verify_inputs(self, record):
        directory = self.directory(record["project_id"], record["id"])
        for name, expected in record["hashes"].items():
            path = (directory / name).resolve()
            if (
                not path.is_relative_to(directory)
                or not path.is_file()
                or file_hash(path) != expected
            ):
                raise QuantStudioError("冻结代码或输入完整性失败：" + name)
