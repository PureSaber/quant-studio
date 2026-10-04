"""Explicit local Python environments for independent upstream applications."""

from __future__ import annotations

import json
import os
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.settings import current_settings


def configured_python(repository: str | None) -> str | None:
    profile = current_settings()
    if profile is not None and repository in profile["python_by_repo"]:
        selected = profile["python_by_repo"][repository]
        if not Path(selected).is_file():
            raise QuantStudioError(f"{repository}配置的Python不存在：{selected}")
        return selected
    path = os.environ.get("QUANT_STUDIO_RUNTIMES")
    if not path or repository is None:
        return None
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuantStudioError(f"无法读取运行环境配置{path}：{exc}") from exc
    if (
        not isinstance(config, dict)
        or set(config) != {"schema_version", "python_by_repo"}
        or config["schema_version"] != "quant-studio.runtimes/v1"
        or not isinstance(config["python_by_repo"], dict)
    ):
        raise QuantStudioError("运行环境配置须为quant-studio.runtimes/v1")
    for repo, executable in config["python_by_repo"].items():
        if not isinstance(executable, str) or not Path(executable).is_absolute():
            raise QuantStudioError(f"{repo}的Python路径须为绝对路径")
    selected = config["python_by_repo"].get(repository)
    if selected is not None and not Path(selected).is_file():
        raise QuantStudioError(f"{repository}配置的Python不存在：{selected}")
    return selected
