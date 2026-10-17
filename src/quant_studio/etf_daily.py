"""Fixed-configuration bridge to the native ETF daily CLI in its own environment."""

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.runtime import configured_python
from quant_studio.settings import setting, subprocess_environment


def configuration():
    operations = setting("QUANT_STUDIO_ETF_OPERATIONS")
    if operations:
        python = configured_python("quant-pipeline-operations")
        if (
            not python
            or not Path(operations).is_absolute()
            or not Path(operations).is_file()
        ):
            raise QuantStudioError("ETF运维配置或指定环境不可用；停止执行，请检查安装")
        return Path(operations), python
    value = setting("QUANT_STUDIO_ETF_DAILY")
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
        "quant_pipeline.etf_operations"
        if setting("QUANT_STUDIO_ETF_OPERATIONS")
        else "quant_pipeline.etf_daily",
        command,
        "--config" if setting("QUANT_STUDIO_ETF_OPERATIONS") else "--profile",
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
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
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
    from quant_studio.etf_status_context import history_context

    path, _ = configuration()
    if value.get("operations"):
        path = Path(json.loads(path.read_text("utf-8"))["daily_profile"])
    value["history_context"] = history_context(path)
    return value


def request(command, plan=None):
    if setting("QUANT_STUDIO_ETF_OPERATIONS"):
        if (
            command != "recover-execute"
            or not isinstance(plan, dict)
            or plan.get("action") not in {"retry", "resume", "reconcile"}
        ):
            raise QuantStudioError("请先查看有效恢复方案，再确认执行")
        path, _ = configuration()
        return {
            "collection_type": "etf-daily",
            "command": command,
            "profile_sha256": file_hash(path),
            "plan": plan,
        }
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
    if payload != request(payload["command"], payload.get("plan")):
        raise QuantStudioError("ETF配置已改变，请重新确认并提交")
    control.stage("native", "调用原生ETF流程；模拟账户与数据门禁独立核验")
    command = argv(payload["command"])
    if payload.get("plan"):
        plan_path = (
            Path(runs_root)
            / ".etf-recovery-plans"
            / (payload["plan"]["sha256"] + ".json")
        )
        if not re.fullmatch(r"[a-f0-9]{64}", payload["plan"]["sha256"]):
            raise QuantStudioError("无效恢复预览摘要")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(payload["plan"], ensure_ascii=False), encoding="utf-8"
        )
        command += ["--plan", str(plan_path)]
    result = _execute_subprocess(
        command,
        cwd=Path(runs_root),
        env=subprocess_environment(),
        timeout=2700,
        control=control,
    )
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise QuantStudioError(
            "ETF流程未返回有效回执：" + result.stderr[-1200:]
        ) from exc
    return {
        "status": "succeeded"
        if result.returncode == 0
        and (not payload.get("plan") or value.get("state") == "completed")
        else "failed",
        "message": (
            f"ETF流程：{value.get('state', 'unknown')}；{value.get('error', '')}"
        ),
        "result_url": "/today",
    }


def preview_recovery(root, operation):
    if not setting("QUANT_STUDIO_ETF_OPERATIONS") or not re.fullmatch(
        r"[a-f0-9]{32}", operation
    ):
        raise QuantStudioError("无效运维记录")
    result = subprocess.run(
        argv("recover-plan") + ["--operation", operation],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        env=subprocess_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    value = json.loads(result.stdout)
    if result.returncode or value.get("schema") != "quant.etf-recovery-plan/v1":
        raise QuantStudioError("恢复预览未通过：" + str(value.get("error")))
    identifier = uuid.uuid4().hex
    directory = Path(root) / ".etf-recovery-plans"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (identifier + ".json")).write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
    return identifier


def read_preview(root, identifier):
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise QuantStudioError("无效恢复预览")
    return json.loads(
        (Path(root) / ".etf-recovery-plans" / (identifier + ".json")).read_text("utf-8")
    )
