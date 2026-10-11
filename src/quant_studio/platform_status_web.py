"""Studio process identity and explicitly timestamped platform observations."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.environment_delivery import DeliveryStore, running_version


def source_identity(package=None):
    package = Path(package or Path(__file__).parent).resolve()
    root = package.parents[1]
    hashes = {}
    for path in sorted(package.rglob("*.py")):
        if path.is_symlink() or not path.resolve().is_relative_to(package):
            raise QuantStudioError("运行源码含链接，无法固定身份")
        hashes[path.relative_to(package).as_posix()] = file_hash(path)
    if not hashes:
        raise QuantStudioError("没有可核验的运行源码")
    revision = None
    dirty = None
    if (root / ".git").exists():
        try:

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(root), *args],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()

            if Path(git("rev-parse", "--show-toplevel")).resolve() == root:
                revision = git("rev-parse", "HEAD")
                dirty = bool(git("status", "--porcelain=v1", "--untracked-files=all"))
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "source_sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
        "python_files": len(hashes),
        "revision": revision,
        "dirty": dirty,
        "lock_sha256": file_hash(root / "requirements.lock")
        if (root / "requirements.lock").is_file()
        else None,
        "scope": (
            "Python source bytes on disk; not an attestation "
            "of loaded memory or other applications"
        ),
    }


def capture_process_identity():
    try:
        installation = running_version()
    except (OSError, ValueError, LookupError, ImportError):
        installation = {"python": sys.executable, "version": None, "installation": None}
    try:
        source = source_identity()
    except (OSError, QuantStudioError) as exc:
        source = {"status": "unverifiable", "reason": str(exc)}
    return {
        "schema_version": "quant-studio.process-identity/v1",
        "pid": os.getpid(),
        "boot_id": uuid.uuid4().hex,
        "captured_at": datetime.now(UTC).isoformat(),
        "installation": installation,
        "startup_source": source,
    }


def process_observation(startup):
    if startup is None:
        return {"status": "not_observed", "reason": "服务启动时未记录身份"}
    result = copy.deepcopy(startup)
    result["observed_at"] = datetime.now(UTC).isoformat()
    result["status"] = (
        "same_process" if startup["pid"] == os.getpid() else "process_mismatch"
    )
    try:
        current = source_identity()
    except (OSError, QuantStudioError) as exc:
        current = {"status": "unverifiable", "reason": str(exc)}
    result["current_disk_source"] = current
    original = startup["startup_source"]
    result["disk_matches_startup"] = (
        current == original
        if "source_sha256" in current and "source_sha256" in original
        else None
    )
    result["scope"] = "由当前处理请求的Studio进程报告；其他Worker和研究环境须分别核验"
    return result


def latest_platform_observation(root):
    records = [
        row
        for row in DeliveryStore(root).list()
        if row["request"]["action"] == "platform-status"
    ]
    if not records:
        return {"status": "not_checked"}
    record = records[0]
    # A newer failure/pending request must never silently fall back to old success.
    if "result_sha256" not in record:
        return {"status": record["status"], "request_id": record["id"]}
    path = DeliveryStore(root).directory(record["id"]) / "result.json"
    if path.stat().st_size > 4_000_000 or file_hash(path) != record["result_sha256"]:
        raise QuantStudioError("平台状态结果损坏或过大")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record["result_sha256"]:
        raise QuantStudioError("平台状态结果在读取时变化")
    result = json.loads(raw)
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "quant.platform-status/v1"
    ):
        return {
            "status": "unavailable",
            "request_id": record["id"],
            "reason": "Workspace未返回平台状态契约",
        }
    return {
        "status": "recorded_snapshot",
        "request_id": record["id"],
        "observation": result,
    }


def platform_status_body(root, startup):
    body = '<header class="page-head"><h1>平台版本与验收</h1></header>'
    body += (
        '<section class="panel"><h2>当前Studio进程</h2>'
        "<p>启动时身份与当前磁盘分别记录。源码变化后，旧进程不会被标成已升级。</p>"
    )
    body += (
        "<pre>"
        + escape(json.dumps(process_observation(startup), ensure_ascii=False, indent=2))
        + "</pre></section>"
    )
    body += (
        '<section class="panel"><h2>最近平台检查</h2>'
        "<p>下方是有时间戳的历史观测；通过“环境与交付”的“核对平台版本与验收”刷新。"
        '发布指针不代表所有服务已切换。</p><a href="/delivery">检查与交付入口</a>'
    )
    try:
        snapshot = latest_platform_observation(root)
    except (OSError, ValueError, KeyError, TypeError, QuantStudioError) as exc:
        snapshot = {"status": "unavailable", "reason": str(exc)}
    return (
        body
        + "<pre>"
        + escape(json.dumps(snapshot, ensure_ascii=False, indent=2))
        + "</pre></section>"
    )
