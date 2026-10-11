"""Explicit project identity over existing stores and a separate Agent process."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from quant_studio.run_record import load_run_result
from quant_studio.runtime import configured_python
from quant_studio.settings import subprocess_environment


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _reference(path, expected=None):
    path = Path(path)
    if path.is_symlink():
        raise QuantStudioError("研究证据不可为符号链接")
    return {"path": str(path.absolute()), "sha256": expected or file_hash(path)}


def _files(directory, names):
    result = []
    for name, expected in names.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise QuantStudioError("研究证据文件越界")
        result.append(_reference(path, expected))
    return result


def review_directory(root, identifier):
    ProjectStore(root).get(identifier)
    path = Path(root).resolve() / ".projects" / identifier / "research-review"
    if path.exists() and path.resolve() != path:
        raise QuantStudioError("研究复核目录不可为链接")
    return path


def collect_project(root, identifier):
    """No search truncation: all exact linked recipe versions and Notebook attempts."""
    root = Path(root).resolve()
    projects, notebooks = ProjectStore(root), NotebookStore(root)
    project = projects.get(identifier)
    history = projects.history(identifier)
    refs = {(r["id"], r["revision"]) for p in history for r in p["recipes"]}
    sources = [_reference(root / ".projects" / identifier / "HEAD")]
    current = {
        "project_revision": project["revision"],
        "datasets": [],
        "recipe_heads": {},
        "notebook_draft_sha256": None,
    }
    missing, attempts = [], []
    for rid in sorted({ref[0] for ref in refs}):
        current["recipe_heads"][rid] = RecipeStore(root).get(rid)["revision"]
    for did in project["datasets"]:
        try:
            dataset = DatasetStore(root).get(did)
            current["datasets"].append({"id": did, "identity": dataset["identity"]})
            base = Path(dataset["path"])
            for name, expected in dataset["hashes"].items():
                source = base if name == "." else base / name
                sources.append(_reference(source, expected))
        except (OSError, ValueError, KeyError, QuantStudioError) as exc:
            missing.append(f"数据集 {did} 无法核验：{exc}")
    draft = notebooks.workspace(identifier) / "research.ipynb"
    if draft.is_file():
        current["notebook_draft_sha256"] = file_hash(draft)
        sources.append(_reference(draft))
    executions = root / ".projects" / identifier / "executions"
    for directory in sorted(executions.glob("*")):
        if not directory.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
            continue
        row = {
            "id": "notebook-" + directory.name,
            "pathway": "notebook",
            "status": "unreadable",
            "files": [],
            "issues": [],
            "comparison_scope": None,
            "link": f"/projects/{identifier}/notebook/{directory.name}",
        }
        try:
            record = notebooks.get(identifier, directory.name)
            row.update(
                status=record["status"],
                project_revision=record["project_revision"],
                message=record.get("message", ""),
                retry_of=record.get("retry_of"),
                integrity_basis="submission hashes and declared output hashes",
                files=[_reference(directory / "run.json")]
                + _files(
                    directory, {**record["hashes"], **record.get("artifacts", {})}
                ),
            )
        except (OSError, ValueError, KeyError, QuantStudioError) as exc:
            row["issues"].append(str(exc))
        attempts.append(row)
    represented = set()
    jobs = []
    for path in sorted((root / ".jobs").glob("*.json")):
        try:
            jobs.append((_json(path), path))
        except (OSError, ValueError) as exc:
            missing.append(f"任务记录 {path.stem} 无法核验：{exc}")
    bound_runs = {
        job["run_id"]: job.get("research_project")
        for job, _ in jobs
        if job.get("run_id")
    }
    for request_path in sorted(root.glob("*/request.json")):
        if request_path.parent.name.startswith("."):
            continue
        try:
            request = _json(request_path)
            recipe = request.get("recipe") or {}
            if (recipe.get("id"), recipe.get("revision")) not in refs:
                continue
            directory = request_path.parent
            binding = bound_runs.get(directory.name)
            if binding and binding["id"] != identifier:
                continue
            represented.add(directory.name)
            result = load_run_result(directory)
            files = [
                _reference(path)
                for path in sorted(directory.rglob("*"))
                if path.is_file()
            ]
            issues = []
            if result.get("verification_status") == "failed":
                issues.append("原生结果核验失败")
            for key, name in (
                ("verification_sha256", "verification.json"),
                ("research_view_sha256", "strategy-output/studio-view/view.json"),
            ):
                if result.get(key):
                    files.append(_reference(directory / name, result[key]))
            provenance = directory / "provenance.json"
            if provenance.exists():
                receipt = _json(provenance)
                code = receipt.get("code", {})
                if code.get("imported_entry") and code.get("entry_sha256"):
                    sources.append(
                        _reference(code["imported_entry"], code["entry_sha256"])
                    )
            attempts.append(
                {
                    "id": "native-" + directory.name,
                    "pathway": "native",
                    "status": result["status"],
                    "files": files,
                    "issues": issues,
                    "comparison_scope": None,
                    "recipe": {"id": recipe["id"], "revision": recipe["revision"]},
                    "association": "explicit_project"
                    if binding
                    else "historical_recipe_version",
                    "project_revision": binding["revision"] if binding else None,
                    "link": f"/experiments/{directory.name}",
                    "message": result.get("message") or "",
                    "integrity_basis": (
                        "current byte snapshot; native sealed checks when declared"
                    ),
                }
            )
        except (OSError, ValueError, KeyError, QuantStudioError) as exc:
            missing.append(f"方案运行记录 {request_path.parent.name} 无法核验：{exc}")
    # Include queued, cancelled and failed-before-run jobs without inventing a run.
    for job, path in jobs:
        try:
            recipe = job.get("recipe") or {}
            binding = job.get("research_project")
            if binding and binding["id"] != identifier:
                continue
            if (recipe.get("id"), recipe.get("revision")) not in refs:
                continue
            if job.get("run_id") in represented:
                continue
            attempts.append(
                {
                    "id": "job-" + job["job_id"],
                    "pathway": "native",
                    "status": job["status"],
                    "files": [_reference(path)],
                    "issues": [],
                    "comparison_scope": None,
                    "link": f"/jobs/{job['job_id']}",
                    "message": job.get("message", ""),
                    "association": "explicit_project"
                    if binding
                    else "historical_recipe_version",
                }
            )
        except (OSError, ValueError, KeyError, QuantStudioError) as exc:
            missing.append(f"任务记录 {path.stem} 无法核验：{exc}")
    bindings = []
    config = os.environ.get("QUANT_STUDIO_RESEARCH_LINKS")
    if config:
        value = _json(config)
        if value.get("schema") != "quant-studio.research-links/v1":
            raise QuantStudioError("研究登记关联配置版本无效")
        bindings = value.get("projects", {}).get(identifier, [])
        sources.append(_reference(Path(config).resolve()))
    return {
        "schema": "quant.research-federation-input/v1",
        "research_id": identifier,
        "question": project["question"],
        "context": current,
        "current_sources": sources,
        "attempts": attempts,
        "lab_bindings": bindings,
        "registration": [
            {
                "kind": "studio_project",
                "status": "exploratory",
                "note": "项目和方案保存不自动构成实验预登记",
            }
        ],
        "missing": missing,
    }


def project_evidence(root, identifier, *, review=None):
    """Use the existing configured Agent runtime, keeping Studio dependency-light."""
    python = configured_python("quant-agent")
    if not python:
        raise QuantStudioError("研究证据工作台需要已配置的 quant-agent 研究流程环境")
    manifest = collect_project(root, identifier)
    argv = [
        python,
        "-I",
        "-X",
        "utf8",
        "-m",
        "quant_agent.research_workflow.cli",
        "federate",
        "--manifest",
        "-",
        "--review-directory",
        str(review_directory(root, identifier)),
    ]
    if review is not None:
        manifest["review_request"] = review
        argv.extend(["--review", "-"])
    try:
        completed = subprocess.run(
            argv,
            input=json.dumps(manifest, ensure_ascii=False),
            capture_output=True,
            encoding="utf-8",
            timeout=120,
            env=subprocess_environment(),
            check=False,
        )
        result = json.loads(completed.stdout)
        if (
            completed.returncode
            or result.get("schema") != "quant.research-federation/v1"
        ):
            raise QuantStudioError(result.get("reason", "研究证据进程未完成核验"))
        if (
            result["research_id"] != identifier
            or result["execution_authorized"] is not False
        ):
            raise QuantStudioError("研究证据返回身份无效")
        return result
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise QuantStudioError("无法读取研究证据，请检查研究环境与已安装版本") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--review", type=Path)
    args = parser.parse_args(argv)
    try:
        result = project_evidence(
            args.runs_root,
            args.project,
            review=_json(args.review) if args.review else None,
        )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (OSError, ValueError, QuantStudioError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
