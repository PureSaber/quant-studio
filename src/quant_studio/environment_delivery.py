"""Review and queue allowlisted Workspace delivery commands from host configuration."""

import importlib.metadata
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.projects import digest
from quant_studio.recipes import _atomic
from quant_studio.settings import setting

ACTIONS = {
    "platform-status": "核对平台版本与验收",
    "doctor": "检查环境缺口",
    "prepare-preview": "预览候选安装",
    "prepare": "安装到新目录",
    "accept": "运行候选验收",
    "activate": "仅登记发布指针（服务切换使用独立控制器）",
    "rollback": "仅登记回滚指针（服务切换使用独立控制器）",
    "verify-transfer": "校验迁移包",
    "restore-transfer": "恢复到新目录",
    "control-status": "核实独立控制器与实际监听进程",
    "verify-backup": "校验完整运维备份",
    "restore-drill": "在隔离新目录验证恢复",
}


def delivery_config():
    raw = setting("QUANT_STUDIO_DELIVERY_CONFIG")
    if not raw:
        raise QuantStudioError("请连接Workspace交付配置后使用此入口")
    try:
        path = Path(raw)
        value = json.loads(path.read_text("utf-8-sig"))
        for key in (
            "python",
            "workspace_config",
            "source_root",
            "profile",
            "state_dir",
        ):
            if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
                raise ValueError(f"{key}必须是服务端绝对路径")
        if not Path(value["python"]).is_file():
            raise ValueError("交付解释器不存在")
        for key in ("maintenance_entrypoint", "maintenance_installation"):
            if value.get(key) and not Path(value[key]).is_absolute():
                raise ValueError("统一维护入口必须是服务端绝对路径")
        if bool(value.get("maintenance_entrypoint")) != bool(
            value.get("maintenance_installation")
        ):
            raise ValueError("统一维护入口与安装配置须同时提供")
        if (
            value.get("local_control")
            and not Path(value["local_control"]).is_absolute()
        ):
            raise ValueError("本地控制器配置必须是服务端绝对路径")
        for name, row in value.get("backups", {}).items():
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                or not isinstance(row, dict)
                or set(row) != {"config"}
                or not Path(row["config"]).is_absolute()
            ):
                raise ValueError("备份必须使用命名的服务端配置")
        for section in ("candidates", "transfers"):
            if not isinstance(value.get(section, {}), dict):
                raise ValueError("候选与迁移配置须为命名对象")
            for name, row in value.get(section, {}).items():
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                    raise ValueError("配置名称无效")
                required = (
                    {"candidate", "prepared_root", "evidence"}
                    if section == "candidates"
                    else {"archive", "destination"}
                )
                if (
                    not isinstance(row, dict)
                    or set(row) != required
                    or any(
                        not isinstance(v, str) or not Path(v).is_absolute()
                        for v in row.values()
                    )
                ):
                    raise ValueError("交付路径须为绝对路径")
        return value, path
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QuantStudioError("交付配置无效：" + str(exc)) from exc


def running_version():
    distribution = importlib.metadata.distribution("quant-studio")
    direct = distribution.read_text("direct_url.json")
    return {
        "pid": os.getpid(),
        "python": sys.executable,
        "version": distribution.version,
        "source": str(Path(__file__).resolve().parent),
        "installation": json.loads(direct) if direct else None,
        "scope": "当前Studio进程；发布指针不等同于进程已重启",
    }


def operational_snapshot():
    """Separate this process's identity from persisted operational intent."""
    from quant_studio.runtime import configured_python

    result = {"studio_process": running_version()}
    try:
        python = configured_python("quant-pipeline-frozen")
        result["frozen_runtime"] = {"configured_python": python}
        if python:
            from quant_workspace.runtime_identity import probe_python

            result["frozen_runtime"]["import_check"] = probe_python(
                python, ("quant-pipeline", "quant-lab", "quant-data-kit")
            )
        else:
            result["frozen_runtime"]["status"] = "未显式配置冻结解释器"
    except (
        OSError,
        ValueError,
        ImportError,
        subprocess.SubprocessError,
        QuantStudioError,
    ) as exc:
        result["frozen_runtime"] = {"status": "无法核实", "error": str(exc)}
    try:
        config, _ = delivery_config()
        pointer = Path(config["state_dir"]) / "current.json"
        result["recorded_release"] = (
            json.loads(pointer.read_text("utf-8")) if pointer.exists() else None
        )
        etf_pointer = Path(config["state_dir"]) / "etf-current.json"
        result["recorded_etf_release"] = (
            json.loads(etf_pointer.read_text("utf-8")) if etf_pointer.exists() else None
        )
        if config.get("local_control"):
            control = json.loads(Path(config["local_control"]).read_text("utf-8-sig"))
            state = Path(control["state_dir"])
            result["local_control"] = {
                "config": config["local_control"],
                "scope": "以下为最近持久化回执；请执行核实操作取得当前监听进程状态",
                **{
                    name: json.loads((state / f"{name}.json").read_text("utf-8"))
                    for name in ("process", "runtime", "last-operation")
                    if (state / f"{name}.json").is_file()
                },
            }
        result["backups"] = {}
        for name, row in config.get("backups", {}).items():
            backup = json.loads(Path(row["config"]).read_text("utf-8-sig"))
            state = Path(backup["state_dir"])
            result["backups"][name] = {
                "config": row["config"],
                "scope": "历史校验回执；不表示当前归档或生产路径已重新核验",
                **{
                    key: json.loads((state / f"{key}.json").read_text("utf-8"))
                    for key in ("backup", "restore")
                    if (state / f"{key}.json").is_file()
                },
            }
    except (OSError, ValueError, KeyError, QuantStudioError) as exc:
        result["delivery_error"] = str(exc)
    return result


class DeliveryStore:
    def __init__(self, root):
        self.root = Path(root).resolve() / ".delivery"

    def directory(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", str(identifier)):
            raise QuantStudioError("交付请求标识无效")
        path = self.root / identifier
        if path.resolve() != path:
            raise QuantStudioError("交付目录不可为链接")
        return path

    def get(self, identifier):
        try:
            value = json.loads(
                (self.directory(identifier) / "request.json").read_text("utf-8")
            )
            if value["id"] != identifier or value["request_sha256"] != digest(
                value["request"]
            ):
                raise QuantStudioError("交付请求身份无效")
            return value
        except (OSError, ValueError, KeyError) as exc:
            raise QuantStudioError("交付请求不存在或损坏") from exc

    def list(self):
        return sorted(
            [self.get(p.name) for p in self.root.glob("*") if p.is_dir()],
            key=lambda r: r["created_at"],
            reverse=True,
        )

    def prepare(self, action, name=""):
        if action not in ACTIONS:
            raise QuantStudioError("不支持此交付操作")
        config, config_path = delivery_config()
        identifier = uuid.uuid4().hex
        directory = self.directory(identifier)
        argv = [
            config["python"],
            "-I",
            "-X",
            "utf8",
            "-m",
            "quant_workspace.cli",
            "--config",
            config["workspace_config"],
            "--root",
            config["source_root"],
        ]
        files = [config_path, Path(config["workspace_config"])]
        if action == "control-status":
            if not config.get("local_control"):
                raise QuantStudioError("请配置独立本机控制器")
            argv = [
                config["python"],
                "-I",
                "-X",
                "utf8",
                "-m",
                "quant_workspace.local_control",
                "--config",
                config["local_control"],
                "status",
            ]
            files.append(Path(config["local_control"]))
        elif action in {"verify-backup", "restore-drill"}:
            target = config.get("backups", {}).get(name)
            if not target:
                raise QuantStudioError("请选择已配置备份")
            backup = json.loads(Path(target["config"]).read_text("utf-8-sig"))
            argv = [
                config["python"],
                "-I",
                "-X",
                "utf8",
                "-m",
                "quant_workspace.operational_backup",
                "--config",
                target["config"],
                "verify" if action == "verify-backup" else "restore-drill",
            ]
            files.extend([Path(target["config"]), Path(backup["archive"])])
        elif action == "platform-status":
            argv += [
                "platform-status",
                "--source-root",
                config["source_root"],
                "--profile",
                config["profile"],
                "--state-dir",
                config["state_dir"],
            ]
            files.append(Path(config["profile"]))
            if name:
                target = config.get("candidates", {}).get(name)
                if not target:
                    raise QuantStudioError("请选择已配置候选")
                argv += [
                    "--candidate",
                    target["candidate"],
                    "--evidence",
                    target["evidence"],
                    "--prepared-root",
                    target["prepared_root"],
                ]
                files.extend([Path(target["candidate"]), Path(target["evidence"])])
        elif action == "doctor":
            argv += ["doctor", "--profile", config["profile"]]
            files.append(Path(config["profile"]))
        elif action.endswith("transfer"):
            target = config.get("transfers", {}).get(name)
            if not target:
                raise QuantStudioError("请选择已配置迁移包")
            argv += [action, "--archive", target["archive"]]
            files.append(Path(target["archive"]))
            if action == "restore-transfer":
                argv += ["--destination", target["destination"]]
        else:
            target = config.get("candidates", {}).get(name)
            if not target:
                raise QuantStudioError("请选择已配置候选")
            files.append(Path(target["candidate"]))
            if action in {"prepare-preview", "prepare"}:
                argv += [
                    "prepare-release",
                    "--candidate",
                    target["candidate"],
                    "--source-root",
                    config["source_root"],
                    "--destination",
                    target["prepared_root"],
                ]
                if action == "prepare":
                    argv += ["--execute", "--build-environments"]
            elif action == "accept":
                argv += [
                    "accept-release",
                    "--candidate",
                    target["candidate"],
                    "--prepared-root",
                    target["prepared_root"],
                    "--out",
                    target["evidence"],
                ]
            else:
                current_path = Path(config["state_dir"]) / "current.json"
                current = (
                    json.loads(current_path.read_text("utf-8"))["candidate_sha256"]
                    if current_path.exists()
                    else "none"
                )
                argv += [
                    action + "-release",
                    "--candidate",
                    target["candidate"],
                    "--evidence",
                    target["evidence"],
                    "--prepared-root",
                    target["prepared_root"],
                    "--state-dir",
                    config["state_dir"],
                    "--expected-current",
                    current,
                ]
                files.append(Path(target["evidence"]))
        request = {
            "action": action,
            "name": name,
            "argv": argv,
            "cwd": config["source_root"],
            "files": {str(p): file_hash(p) for p in files},
        }
        record = {
            "id": identifier,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "prepared",
            "request": request,
            "request_sha256": digest(request),
        }
        directory.mkdir(parents=True)
        _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
        return record

    def execute(self, identifier, control):
        from quant_studio.runner import _execute_subprocess
        from quant_studio.settings import subprocess_environment

        record = self.get(identifier)
        if record["status"] not in {"prepared", "queued"}:
            raise QuantStudioError("交付请求已执行，请新建预览")
        directory = self.directory(identifier)
        try:
            for filename, expected in record["request"]["files"].items():
                if file_hash(filename) != expected:
                    raise QuantStudioError("预览后的配置或候选文件已变化，请重新预览")
            record["status"] = "running"
            _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
            result = _execute_subprocess(
                record["request"]["argv"],
                cwd=record["request"]["cwd"],
                env=subprocess_environment(),
                timeout=3600,
                control=control,
            )
            output = json.loads(result.stdout)
            _atomic(
                directory / "result.json",
                json.dumps(output, ensure_ascii=False, indent=2),
            )
            record.update(
                status="succeeded" if result.returncode == 0 else "failed",
                result_sha256=file_hash(directory / "result.json"),
                message="Workspace操作完成"
                if result.returncode == 0
                else "Workspace报告未通过",
            )
        except Exception as exc:
            record.update(status="failed", message=str(exc))
            raise
        finally:
            _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
        return {"status": record["status"], "message": record["message"]}
