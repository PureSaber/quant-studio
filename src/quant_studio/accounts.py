"""Explicit account sources inspected through a separate, read-only upstream CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.runtime import configured_python


@dataclass(frozen=True)
class AccountSource:
    id: str
    label: str
    path: Path


def account_sources() -> list[AccountSource]:
    value = os.environ.get("QUANT_STUDIO_ACCOUNTS")
    if not value:
        return []
    try:
        config = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuantStudioError(f"无法读取账户配置：{exc}") from exc
    if (
        not isinstance(config, dict)
        or set(config) != {"schema_version", "accounts"}
        or config["schema_version"] != "quant-studio.accounts/v1"
        or not isinstance(config["accounts"], list)
    ):
        raise QuantStudioError("账户配置须为quant-studio.accounts/v1")
    sources = []
    seen = set()
    for item in config["accounts"]:
        if not isinstance(item, dict) or set(item) != {"id", "label", "path"}:
            raise QuantStudioError("每个账户须声明id、label和path")
        if (
            not isinstance(item["id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", item["id"])
            or item["id"] in seen
            or not isinstance(item["label"], str)
            or not item["label"].strip()
            or not isinstance(item["path"], str)
            or not Path(item["path"]).is_absolute()
        ):
            raise QuantStudioError("账户须有唯一简短ID、名称和绝对目录路径")
        seen.add(item["id"])
        sources.append(AccountSource(item["id"], item["label"], Path(item["path"])))
    return sources


def inspect_source(source: AccountSource, *, timeout: float = 30) -> dict:
    python = configured_python("quant-pipeline") or sys.executable
    if not source.path.is_dir():
        raise QuantStudioError(
            f"账户目录不存在：{source.path}。请修正账户配置中的path。"
        )
    argv = [
        python,
        "-I",
        "-B",
        "-X",
        "utf8",
        "-m",
        "quant_pipeline.research_paper",
        "inspect",
        str(source.path),
    ]
    try:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QuantStudioError(f"账户只读核验未完成：{exc}") from exc
    if result.returncode:
        detail = result.stderr.strip()[-1600:] or result.stdout.strip()[-1600:]
        raise QuantStudioError(
            "账户只读核验失败。请检查保存的证据及quant-pipeline维护环境；"
            f"不要重新登记或覆盖旧账户。\n{detail}"
        )
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise QuantStudioError("账户核验器没有返回有效JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "quant.research-paper-inspection/v1"
        or value.get("read_only") is not True
        or value.get("verification") != "saved_registration_and_observation_artifacts"
        or not isinstance(value.get("observations"), list)
        or not isinstance(value.get("attempts"), list)
        or value.get("state")
        not in {
            "pending",
            "awaiting_observation",
            "observing",
            "ended_unsealed",
            "sealed",
        }
        or not isinstance(value.get("window"), dict)
        or not all(
            key in value for key in ("account_id", "created_at", "definition_sha256")
        )
    ):
        raise QuantStudioError("账户核验器返回了不兼容的只读契约")
    return {**value, "inspection_python": python}
