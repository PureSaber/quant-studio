"""Fixed-configuration bridge to the native ETF daily CLI in its own environment."""

import json
import os
import subprocess
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.runtime import configured_python
from quant_studio.settings import subprocess_environment


def configuration():
    value = os.environ.get("QUANT_STUDIO_ETF_DAILY")
    python = configured_python("quant-pipeline-frozen") or configured_python(
        "quant-pipeline"
    )
    if not value or not python:
        raise QuantStudioError("尚未连接ETF每日配置或quant-pipeline独立环境")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise QuantStudioError("ETF每日配置须为已存在的绝对文件路径")
    return path, python


def argv(command):
    path, python = configuration()
    return [
        python,
        "-I",
        "-B",
        "-X",
        "utf8",
        "-m",
        "quant_pipeline.etf_daily",
        command,
        "--profile",
        str(path),
    ]


def inspect_daily():
    try:
        result = subprocess.run(
            argv("status"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=subprocess_environment(),
            check=False,
        )
        value = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise QuantStudioError(f"ETF每日状态无法核验：{exc}") from exc
    if (
        result.returncode
        or value.get("schema") != "quant.etf-daily-status/v1"
        or value.get("read_only") is not True
    ):
        raise QuantStudioError(
            "ETF每日状态核验失败：" + str(value.get("error", result.stderr[-1000:]))
        )
    return value


def request(command):
    if command not in {"refresh", "observe"}:
        raise QuantStudioError("未知ETF操作")
    path, _ = configuration()
    return {
        "collection_type": "etf-daily",
        "command": command,
        "profile_sha256": file_hash(path),
    }


def execute_daily(runs_root, job, control):
    from quant_studio.runner import _execute_subprocess

    payload = job["collection"]
    if payload != request(payload["command"]):
        raise QuantStudioError("ETF配置已改变，请重新确认并提交")
    control.stage("native", "调用原生ETF流程；模拟账户与数据门禁独立核验")
    result = _execute_subprocess(
        argv(payload["command"]),
        cwd=Path(runs_root),
        env=subprocess_environment(),
        timeout=1800,
        control=control,
    )
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise QuantStudioError(
            "ETF流程未返回有效回执：" + result.stderr[-1200:]
        ) from exc
    return {
        "status": "succeeded" if result.returncode == 0 else "failed",
        "message": (
            f"ETF流程：{value.get('state', 'unknown')}；{value.get('error', '')}"
        ),
        "result_url": "/today",
    }
