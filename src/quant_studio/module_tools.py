"""Owner-operated scaffolding and validated module registration; never a web upload."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.recipes import _atomic, parse_config
from quant_studio.templates import _validate_custom

EXAMPLE = (
    files("quant_studio")
    .joinpath("assets/research_example.py.txt")
    .read_text(encoding="utf-8")
)


def initialize(project, identifier):
    project = Path(project).resolve()
    if not re.fullmatch(r"custom-[a-z0-9-]{1,70}", identifier) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,79}", project.name
    ):
        raise QuantStudioError(
            "模块须使用 custom- 前缀；项目目录使用小写字母、数字或连字符"
        )
    if project.exists():
        raise QuantStudioError("项目目录已存在；不会覆盖已有代码")
    project.mkdir(parents=True)
    directory = project / ".studio"
    directory.mkdir()
    module = project.name.replace("-", "_")
    metadata = {
        "id": identifier,
        "title": "我的 Python 研究",
        "summary": "本地开发的研究模块；初始内容为合成样例。",
        "kind": "external",
        "workspace_repo": project.name,
        "upstream_ref": "owner-module",
        "base_config": "base.json",
        "config_format": "json",
        "knobs": [],
        "argv": [
            "python",
            "-m",
            module,
            "--config",
            "{config}",
            "--output",
            "{output}",
        ],
        "preflight_argv": [
            "python",
            "-m",
            module,
            "--config",
            "{config}",
            "--preflight",
        ],
        "preflight_contract": {
            "schema_version": "quant-studio.custom-preflight/v1",
            "status_field": "software_preflight",
            "positive_counts": ["rows"],
        },
        "output_dirs": ["{output}"],
        "result_files": {"nav.csv": "nav.csv", "report.html": "report.html"},
        "nav_initial_value_key": "initial_capital",
        "report_name": "report.html",
        "disclaimer": "合成接入样例；研究用途。",
    }
    (directory / "template.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "base.json").write_text('{"initial_capital":100000}', encoding="utf-8")
    (project / f"{module}.py").write_text(EXAMPLE, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        f'''[build-system]
requires = ["setuptools==84.0.0"]
build-backend = "setuptools.build_meta"
[project]
name = "{project.name}"
version = "0.1.0"
requires-python = ">=3.12"
[tool.setuptools]
py-modules = ["{module}"]
''',
        encoding="utf-8",
    )
    return {"project": str(project), "id": identifier}


def register(project, registry, *, python=sys.executable, install=True, settings=None):
    project, registry = Path(project).resolve(), Path(registry).resolve()
    try:
        metadata = json.loads(
            (project / ".studio/template.json").read_text(encoding="utf-8")
        )
        identifier = metadata["id"]
        _validate_custom(metadata, identifier)
        if (
            not re.fullmatch(r"custom-[a-z0-9-]{1,70}", identifier)
            or metadata["workspace_repo"] != project.name
        ):
            raise QuantStudioError("模块声明与项目目录不一致")
        config_text = (project / ".studio" / metadata["base_config"]).read_text(
            encoding="utf-8"
        )
        parse_config(config_text)
    except (OSError, ValueError, KeyError) as exc:
        raise QuantStudioError(f"模块声明无效：{exc}") from exc
    target = registry / identifier
    if target.exists():
        previous = json.loads((target / "template.json").read_text(encoding="utf-8"))
        if previous != metadata:
            raise QuantStudioError(
                "接口声明已变化，请使用新的 custom- 标识，以保留旧方案可读取"
            )
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if install:
        outcome = subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-build-isolation",
                "-e",
                str(project),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=environment,
        )
        if outcome.returncode:
            raise QuantStudioError("模块安装失败：" + outcome.stderr[-2000:])
    with tempfile.TemporaryDirectory(prefix="studio-module-") as temporary:
        config = Path(temporary) / metadata["base_config"]
        config.write_text(config_text, encoding="utf-8")
        argv = [
            str(python) if v == "python" else str(config) if v == "{config}" else v
            for v in metadata["preflight_argv"]
        ]
        outcome = subprocess.run(
            argv,
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    if outcome.returncode:
        raise QuantStudioError("模块预检失败：" + outcome.stderr[-2000:])
    try:
        preflight = json.loads(outcome.stdout)
        from quant_studio.runner import _validate_preflight
        from quant_studio.templates import Template

        _validate_preflight(
            Template(metadata, parse_config(config_text), project / ".studio"),
            preflight,
        )
    except (ValueError, KeyError, AttributeError) as exc:
        raise QuantStudioError(f"预检未返回支持的 JSON：{exc}") from exc
    target.mkdir(parents=True, exist_ok=True)
    _atomic(target / metadata["base_config"], config_text)
    # Publish manifest last so a newly registered module is never partially visible.
    _atomic(
        target / "template.json", json.dumps(metadata, ensure_ascii=False, indent=2)
    )
    receipt = {
        "id": identifier,
        "project": str(project),
        "python": str(python),
        "preflight": preflight,
    }
    _atomic(
        target / "registration.json", json.dumps(receipt, ensure_ascii=False, indent=2)
    )
    if settings:
        from quant_studio.setup import empty_profile

        settings_path = Path(settings).resolve()
        profile = (
            json.loads(settings_path.read_text(encoding="utf-8-sig"))
            if settings_path.exists()
            else empty_profile()
        )
        if profile.get("schema_version") != "quant-studio.settings/v1":
            raise QuantStudioError("工作台配置格式错误；模块已登记，未修改环境配置")
        workspace = profile["environment"].get("QUANT_WORKSPACE_ROOT")
        if workspace and Path(workspace).resolve() != project.parent:
            raise QuantStudioError(
                "模块目录须位于工作台工作区下；模块已登记，未修改环境配置"
            )
        profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(project.parent)
        profile["python_by_repo"][project.name] = str(Path(python).resolve())
        _atomic(settings_path, json.dumps(profile, ensure_ascii=False, indent=2))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="创建和登记本机 Python 研究模块")
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    init.add_argument("--project", required=True)
    init.add_argument("--id", required=True)
    publish = commands.add_parser("register")
    publish.add_argument("--project", required=True)
    publish.add_argument("--registry", required=True)
    publish.add_argument("--python", default=sys.executable)
    publish.add_argument("--settings", help="同时连接此工作台的运行环境配置")
    args = parser.parse_args(argv)
    result = (
        initialize(args.project, args.id)
        if args.action == "init"
        else register(
            args.project, args.registry, python=args.python, settings=args.settings
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
