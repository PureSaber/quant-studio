"""Single-owner supervision, integrity-checked state backups and health visibility."""

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import time
import uuid
from datetime import UTC, datetime
from html import escape
from http.client import HTTPSConnection
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash, read_json
from quant_studio.recipes import _atomic


def _backup_files(state):
    selected = []
    for name in ("runs", "modules"):
        folder = state / name
        if folder.exists():
            selected += [
                p
                for p in folder.rglob("*")
                if p.is_file()
                and p.name != "manager.lock"
                and p.suffix not in {".tmp", ".lock"}
            ]
    selected += [
        state / name
        for name in ("studio-settings.json", "service.json")
        if (state / name).is_file()
    ]
    for path in selected:
        if not path.resolve().is_relative_to(state.resolve()) or path.is_symlink():
            raise QuantStudioError("备份路径越界或含链接")
    return sorted(selected)


def _idle(state):
    for job in (state / "runs/.jobs").glob("*.json"):
        if read_json(job).get("status") in {"queued", "running", "cancelling"}:
            raise QuantStudioError("仍有任务活动，延后备份")


def backup_state(state):
    state = Path(state).resolve()
    _idle(state)
    paths = _backup_files(state)
    if not paths:
        raise QuantStudioError("没有可备份的研究状态")
    total = sum(p.stat().st_size for p in paths)
    if shutil.disk_usage(state).free < total * 2 + 1024**3:
        raise QuantStudioError("可用空间不足，未删除旧备份")
    before = {p.relative_to(state).as_posix(): file_hash(p) for p in paths}
    backups = state / "backups"
    backups.mkdir(exist_ok=True)
    target = (
        backups
        / f"workbench-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.zip"
    )
    temporary = target.with_suffix(".partial")
    with ZipFile(temporary, "w", ZIP_DEFLATED) as zipped:
        for path in paths:
            zipped.write(path, path.relative_to(state).as_posix())
        zipped.writestr("MANIFEST.sha256.json", json.dumps(before, sort_keys=True))
    _idle(state)
    after = {
        p.relative_to(state).as_posix(): file_hash(p) for p in _backup_files(state)
    }
    if before != after:
        raise QuantStudioError("备份期间文件变化，保留 partial 诊断包，稍后重试")
    verification = verify_backup(temporary)
    temporary.rename(target)
    return {
        "archive": str(target),
        "sha256": file_hash(target),
        "files": verification["files"],
        "created_at": datetime.now(UTC).isoformat(),
        "bytes": target.stat().st_size,
        "scope": (
            "工作台状态、已管理的采集快照和模块声明；"
            "外部数据、源码环境和私密凭据须单独保存"
        ),
    }


def verify_backup(archive, *, restore=None):
    with ZipFile(archive) as zipped:
        names = zipped.namelist()
        if len(names) != len(set(names)) or "MANIFEST.sha256.json" not in names:
            raise QuantStudioError("备份清单重复或缺失")
        manifest = json.loads(zipped.read("MANIFEST.sha256.json"))
        if set(manifest) != set(names) - {"MANIFEST.sha256.json"}:
            raise QuantStudioError("归档内容与清单不一致")
        for name, digest in manifest.items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
                raise QuantStudioError("归档清单含越界路径")
            with zipped.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise QuantStudioError("归档文件校验失败：" + name)
        if restore:
            destination = Path(restore).resolve()
            if destination.exists():
                raise QuantStudioError("恢复目录必须不存在，以免覆盖现有研究")
            destination.mkdir(parents=True)
            zipped.extractall(destination)
    return {"files": len(manifest), "valid": True}


def health(config):
    context = ssl.create_default_context(cafile=config["ca"])
    connection = HTTPSConnection(
        config["host"], config["port"], context=context, timeout=4
    )
    try:
        connection.connect()
        certificate = connection.sock.getpeercert()
        expires = ssl.cert_time_to_seconds(certificate["notAfter"])
        connection.request("GET", "/login")
        response = connection.getresponse()
        response.read()
        return {
            "reachable": response.status == 200,
            "certificate_days": int((expires - time.time()) / 86400),
            "checked_at": datetime.now(UTC).isoformat(),
            "scope": "本机访问服务地址；不替代第二台设备连通检查",
        }
    except (OSError, ValueError) as exc:
        return {
            "reachable": False,
            "error": str(exc),
            "checked_at": datetime.now(UTC).isoformat(),
        }
    finally:
        connection.close()


def supervise(config_path):
    from quant_studio.jobs import (
        _acquire_root_lock,
        _release_root_lock,
        _terminate_process_scope,
    )

    path = Path(config_path).resolve()
    config = read_json(path)
    state = path.parent
    lock = _acquire_root_lock(state / "supervisor.lock")
    stop = state / "stop-supervisor.request"
    child_stop = Path(config["stop_file"])
    logs = state / "logs"
    logs.mkdir(exist_ok=True)
    process = None
    status_path = state / "operations-status.json"
    status = read_json(status_path) if status_path.exists() else {}
    status.update(supervisor_pid=os.getpid(), status="starting", restarts=0)
    next_start, next_health, next_backup = 0, 0, 0
    last_backup = status.get("backup", {}).get("created_at")
    if last_backup:
        next_backup = datetime.fromisoformat(last_backup).timestamp() + config.get(
            "backup_interval_seconds", 86400
        )
    attempts = 0
    try:
        while not stop.exists():
            now = time.time()
            if process is not None and process.poll() is not None:
                status.update(status="retrying", last_exit=process.returncode)
                process = None
                attempts += 1
                next_start = now + min(60, 2 ** min(attempts, 6))
            if process is None and now >= next_start:
                if child_stop.exists():
                    child_stop.unlink()
                args = [
                    config["python"],
                    "-X",
                    "utf8",
                    "-m",
                    "quant_studio",
                    "serve",
                    "--host",
                    config["host"],
                    "--port",
                    str(config["port"]),
                    "--runs-root",
                    str(state / "runs"),
                    "--settings",
                    str(state / "studio-settings.json"),
                    "--access",
                    config["access"],
                    "--tls-cert",
                    config["cert"],
                    "--tls-key",
                    config["key"],
                    "--stop-file",
                    str(child_stop),
                    "--templates",
                    str(state / "modules"),
                ]
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                with (
                    (logs / f"server-{stamp}.out.log").open("ab") as out,
                    (logs / f"server-{stamp}.err.log").open("ab") as err,
                ):
                    process = subprocess.Popen(
                        args,
                        cwd=config["cwd"],
                        stdout=out,
                        stderr=err,
                        creationflags=(
                            subprocess.CREATE_NEW_PROCESS_GROUP
                            | subprocess.CREATE_NO_WINDOW
                        )
                        if os.name == "nt"
                        else 0,
                        start_new_session=os.name != "nt",
                    )
                status.update(
                    status="running", server_pid=process.pid, restarts=attempts
                )
            if now >= next_health:
                status["health"] = health(config)
                status["free_disk_gb"] = round(
                    shutil.disk_usage(state).free / 1024**3, 1
                )
                next_health = now + 30
            if now >= next_backup:
                try:
                    status["backup"] = backup_state(state)
                    status.pop("backup_error", None)
                    next_backup = now + config.get("backup_interval_seconds", 86400)
                except (OSError, ValueError, QuantStudioError) as exc:
                    status["backup_error"] = str(exc)
                    next_backup = now + 300
            status["updated_at"] = datetime.now(UTC).isoformat()
            _atomic(status_path, json.dumps(status, ensure_ascii=False, indent=2))
            time.sleep(1)
    finally:
        if process is not None and process.poll() is None:
            child_stop.parent.mkdir(parents=True, exist_ok=True)
            child_stop.write_text("stop", encoding="utf-8")
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                _terminate_process_scope(process, force=True)
        status.update(status="stopped", updated_at=datetime.now(UTC).isoformat())
        _atomic(status_path, json.dumps(status, ensure_ascii=False, indent=2))
        _release_root_lock(lock)


def operations_body(runs_root):
    state = Path(runs_root).parent
    status_path = state / "operations-status.json"
    status = read_json(status_path) if status_path.exists() else {}
    checks = status.get("health", {})
    days = checks.get("certificate_days")
    certificate = f"{days} 天后到期" if days is not None else "尚未取得证书检查结果"
    if days is not None and days <= 14:
        certificate += "；请尽快更新证书"
    backup = status.get("backup", {})
    stale = (
        not status.get("updated_at")
        or time.time() - datetime.fromisoformat(status["updated_at"]).timestamp() > 90
    )
    condition = "监护进程未配置或心跳已过期" if stale else status.get("status", "未知")
    return f"""<header
class="page-head">
<h1>服务与备份</h1>
<p>本机健康状态和自动备份；真实跨设备连接需在客户端验收。</p>
</header>
<section
class="panel">
<h2>运行状态</h2>
<p>服务监护：{escape(condition)}<br>
健康检查：{"通过" if checks.get("reachable") and not stale else "未通过或待检查"}<br>
证书：{escape(certificate)}<br>
异常退出重启次数：{status.get("restarts", 0)}<br>
可用磁盘：{status.get("free_disk_gb", "未知")} GB</p>
<p>记录时间：{escape(status.get("updated_at", "无"))}</p>
</section>
<section
class="panel">
<h2>备份</h2>
<p>最近成功：{escape(backup.get("created_at", "尚无"))}<br>
文件：{backup.get("files", "未知")}<br>
{escape(status.get("backup_error", ""))}</p>
<p
class="note">监护开启后，每 24 小时在任务空闲时备份。
文件变化时延后，不删除旧备份；外部数据、源码和私密凭据须另行保存。</p>
<details>
<summary>备份路径与摘要</summary>
<pre>{escape(json.dumps(backup, ensure_ascii=False, indent=2))}</pre>
</details>
</section>
<section
class="panel">
<h2>跨设备接入检查</h2>
<ol>
<li>客户端通过同一局域网或你明确配置的受控组网连接服务地址。</li>
<li>信任公开 CA，检查 Windows 防火墙规则。</li>
<li>登录、提交合成任务，再次登录确认结果。</li>
</ol>
<p>普通上网 VPN 不会自动开放本机。
关机、睡眠、退出用户会话或网络中断会影响服务；
当前用户自启在登录 Windows 后生效。</p>
</section>"""


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    monitor = sub.add_parser("supervise")
    monitor.add_argument("--config", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--state", required=True)
    verify = sub.add_parser("verify-backup")
    verify.add_argument("--archive", required=True)
    verify.add_argument("--restore")
    args = parser.parse_args(argv)
    if args.action == "supervise":
        supervise(args.config)
        return
    result = (
        backup_state(args.state)
        if args.action == "backup"
        else verify_backup(args.archive, restore=args.restore)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
