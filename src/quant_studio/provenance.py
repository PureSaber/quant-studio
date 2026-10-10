"""Capture identities without claiming external files have been archived."""

import json
import subprocess
import sys
from pathlib import Path

from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.recipes import _atomic
from quant_studio.runtime import template_python
from quant_studio.settings import setting


def capture(run_dir, template, snapshot, *, probe=False):
    configured = template_python(template)
    receipt = {
        "schema": "quant-studio.provenance/v1",
        "python": configured or sys.executable,
        "template": template.id,
        "code": {},
        "input": {},
        "environment_locks": {},
        "scope": (
            "运行前的身份记录；外部行情文件与源码未复制封存。完整原生核验以报告为准。"
        ),
    }
    workspace = setting("QUANT_WORKSPACE_ROOT")
    checkout = (
        Path(workspace) / template.metadata.get("workspace_repo", "")
        if workspace
        else None
    )
    receipt["code"]["configured_checkout"] = str(checkout) if checkout else None
    source = None
    declared = template.metadata.get("argv", [])
    module = (
        declared[2]
        if declared[:2] == ["python", "-m"]
        else str(template.metadata.get("workspace_repo", "")).replace("-", "_")
    )
    receipt["code"]["import_probe"] = (
        "仅实际运行且明确选择 Python 环境时探测源码；此记录尚未探测"
    )
    if probe and configured and module:
        try:
            program = (
                "import importlib.util,sys; "
                "s=importlib.util.find_spec(sys.argv[1]); print(s.origin if s else '')"
            )
            origin = subprocess.check_output(
                [receipt["python"], "-I", "-c", program, module],
                timeout=5,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
            ).strip()
            if origin and Path(origin).is_file():
                receipt["code"].pop("import_probe", None)
                receipt["code"]["imported_entry"] = origin
                receipt["code"]["entry_sha256"] = file_hash(origin)
                source = next(
                    (
                        p
                        for p in Path(origin).parents
                        if (p / "pyproject.toml").exists() or (p / ".git").exists()
                    ),
                    None,
                )
        except (OSError, subprocess.SubprocessError):
            receipt["code"]["import_probe"] = "无法确认实际导入位置；不以配置目录替代"
    if source and source.is_dir():
        receipt["code"]["path"] = str(source)
        try:

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(source), *args],
                    timeout=3,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                ).strip()

            receipt["code"].update(
                commit=git("rev-parse", "HEAD"),
                clean=not bool(git("status", "--porcelain")),
            )
        except (OSError, subprocess.SubprocessError):
            receipt["code"]["commit"] = "未登记 Git 身份"
        for name in ("requirements.lock", "stack.lock", "pyproject.toml"):
            if (source / name).is_file():
                receipt["environment_locks"][name] = file_hash(source / name)
    recipe = template.metadata.get("recipe") or {}
    if recipe.get("dataset_id"):
        item = DatasetStore(Path(run_dir).parent).get(recipe["dataset_id"])
        receipt["input"] = {
            k: item[k]
            for k in (
                "id",
                "name",
                "path",
                "identity",
                "provider",
                "start",
                "end",
                "hashes",
            )
        }
    elif snapshot:
        path = Path(snapshot)
        receipt["input"]["path"] = str(path)
        marker = path if path.is_file() else path / "manifest.json"
        if marker.is_file():
            receipt["input"]["manifest_or_config_sha256"] = file_hash(marker)
    _atomic(
        Path(run_dir) / "provenance.json",
        json.dumps(receipt, ensure_ascii=False, indent=2),
    )
