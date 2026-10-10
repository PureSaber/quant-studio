"""Local onboarding over existing runtime, input and account contracts."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock

from quant_studio import QuantStudioError
from quant_studio.settings import use_settings
from quant_studio.templates import load_template, template_ids

SCHEMA = "quant-studio.settings/v1"


def repositories() -> list[str]:
    return sorted(
        {
            load_template(key).metadata.get("runtime_key")
            or load_template(key).metadata.get("workspace_repo")
            for key in template_ids()
        }
        - {None}
    ) + ["quant-pipeline"]


def input_sources() -> dict[str, dict]:
    return {
        source["environment"]: source
        for key in template_ids()
        if (source := load_template(key).metadata.get("input_source"))
    }


def empty_profile() -> dict:
    return {"schema_version": SCHEMA, "environment": {}, "python_by_repo": {}}


def environment_keys() -> set[str]:
    return {"QUANT_WORKSPACE_ROOT", "QUANT_STUDIO_ACCOUNTS", *input_sources()}


def validate_shape(profile: object) -> None:
    if (
        not isinstance(profile, dict)
        or set(profile) != {"schema_version", "environment", "python_by_repo"}
        or profile["schema_version"] != SCHEMA
        or not isinstance(profile["environment"], dict)
        or not isinstance(profile["python_by_repo"], dict)
        or set(profile["environment"]) - environment_keys()
    ):
        raise QuantStudioError(
            "配置格式须为quant-studio.settings/v1；请选用新的配置文件"
        )
    if set(profile["python_by_repo"]) - set(repositories()):
        raise QuantStudioError("配置含有未支持的仓库")
    for name, value in (profile["environment"] | profile["python_by_repo"]).items():
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise QuantStudioError(f"{name}须为非空绝对路径；留空时沿用启动配置")


def inherited_profile() -> dict:
    from quant_studio.runtime import configured_python

    profile = empty_profile()
    profile["environment"] = {
        key: value for key in environment_keys() if (value := os.environ.get(key))
    }
    for repo in repositories():
        if selected := configured_python(repo):
            profile["python_by_repo"][repo] = selected
    return profile


def profile_from_form(form: dict[str, list[str]]) -> dict:
    names = {"workspace": "QUANT_WORKSPACE_ROOT", "accounts": "QUANT_STUDIO_ACCOUNTS"}
    names.update({f"input:{key}": key for key in input_sources()})
    allowed = set(names) | {f"python:{repo}" for repo in repositories()}
    if set(form) - allowed or any(len(values) != 1 for values in form.values()):
        raise QuantStudioError("配置字段未知或重复；请重新填写")
    profile = empty_profile()
    for key, values in form.items():
        value = values[0].strip()
        if not value:
            continue
        if key in names:
            profile["environment"][names[key]] = value
        else:
            profile["python_by_repo"][key.removeprefix("python:")] = value
    return profile


def _python_check(path: str) -> None:
    if not Path(path).is_file():
        raise QuantStudioError(
            f"Python不存在：{path}。请填写已有环境的Python可执行文件"
        )
    try:
        completed = subprocess.run(
            [
                path,
                "-I",
                "-B",
                "-X",
                "utf8",
                "-c",
                "import json,sys; "
                "print(json.dumps([sys.executable,sys.version_info.major]))",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        executable, major = json.loads(completed.stdout)
        if (
            completed.returncode
            or major != 3
            or Path(executable).resolve() != Path(path).resolve()
        ):
            raise ValueError("not selected Python")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise QuantStudioError(
            f"无法启动所选Python：{path}。请检查已有环境，不会自动安装"
        ) from exc


def validate_profile(profile: dict) -> list[tuple[str, str]]:
    from quant_studio.accounts import account_sources
    from quant_studio.runner import template_readiness
    from quant_studio.settings import setting

    validate_shape(profile)
    for key, value in profile["environment"].items():
        path = Path(value)
        source = input_sources().get(key)
        file = key == "QUANT_STUDIO_ACCOUNTS" or (
            source and source.get("kind") == "file"
        )
        if not (path.is_file() if file else path.is_dir()):
            raise QuantStudioError(
                f"路径不存在或类型错误：{value}。请选择已有{'文件' if file else '目录'}"
            )
        if source and not file:
            missing = [
                name for name in source["required_files"] if not (path / name).is_file()
            ]
            if missing:
                raise QuantStudioError(
                    f"{source['label']}缺少{', '.join(missing)}；请更换完整输入目录"
                )
    for path in set(profile["python_by_repo"].values()):
        _python_check(path)
    checks = []
    with use_settings(profile):
        root = setting("QUANT_WORKSPACE_ROOT")
        for repo in profile["python_by_repo"]:
            checkout = next(
                (
                    load_template(key).metadata.get("workspace_repo")
                    for key in template_ids()
                    if load_template(key).metadata.get("runtime_key") == repo
                ),
                repo,
            )
            if not root or not (Path(root) / checkout).is_dir():
                raise QuantStudioError(
                    f"工作区缺少{repo}；请先填写含该仓库的工作区目录"
                )
        for key in template_ids():
            template = load_template(key)
            ready = template_readiness(template)
            source = template.metadata.get("input_source", {})
            selected_input = profile["environment"].get(source.get("environment"))
            selected_python = (
                template.metadata.get("runtime_key")
                or template.metadata.get("workspace_repo")
            ) in profile["python_by_repo"]
            if not ready.runnable and (
                selected_input or (selected_python and not ready.needs_input)
            ):
                raise QuantStudioError(
                    f"{template.title}：{ready.message}。请修正环境或已有输入路径"
                )
            checks.append((template.title, ready.message))
        for account in account_sources():
            if not account.path.is_dir():
                raise QuantStudioError(
                    f"账户目录不存在：{account.path}。请修正已有账户配置"
                )
            checks.append(
                (f"账户：{account.label}", "来源已连接；打开账户时执行原生只读核验")
            )
    return checks


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path).absolute()
        self._lock = RLock()
        self._pending: dict[str, tuple[dict, bytes | None]] = {}
        self._bytes = self._read()
        self._profile = None
        if self._bytes is not None:
            try:
                self._profile = json.loads(self._bytes)
                validate_shape(self._profile)
            except (ValueError, QuantStudioError) as exc:
                raise QuantStudioError(f"无法恢复本地配置{self.path}：{exc}") from exc

    def _read(self) -> bytes | None:
        # Reject redirected files and directories before creating or replacing anything.
        for path in [self.path, *self.path.parents]:
            if path.is_symlink() or (
                hasattr(path, "is_junction") and path.is_junction()
            ):
                raise QuantStudioError("配置保存路径不能包含符号链接或目录联接")
        if self.path.exists() and (
            not self.path.is_file() or self.path.stat().st_nlink > 1
        ):
            raise QuantStudioError("配置保存路径须为独立的普通文件")
        try:
            return self.path.read_bytes() if self.path.exists() else None
        except OSError as exc:
            raise QuantStudioError(f"无法读取本地配置：{exc}") from exc

    def snapshot(self) -> dict | None:
        with self._lock:
            return deepcopy(self._profile)

    def review(self, profile: dict) -> tuple[str, list[tuple[str, str]]]:
        checks = validate_profile(profile)
        with self._lock:
            if self._read() != self._bytes:
                raise QuantStudioError("配置文件已变化；请重启读取后重新校验")
            if len(self._pending) >= 32:
                self._pending.pop(next(iter(self._pending)))
            token = secrets.token_urlsafe(32)
            self._pending[token] = (deepcopy(profile), self._bytes)
            return token, checks

    def reviewed(self, token: str) -> dict:
        with self._lock:
            if token not in self._pending:
                raise QuantStudioError("预览已失效；请重新校验配置")
            return deepcopy(self._pending[token][0])

    def save(self, token: str) -> None:
        with self._lock:
            profile = self.reviewed(token)
            baseline = self._pending[token][1]
            validate_profile(profile)
            if self._read() != baseline or self._bytes != baseline:
                raise QuantStudioError("配置文件已变化；请重新校验配置")
            payload = (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=self.path.parent, delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if self._read() != baseline:
                    raise QuantStudioError("配置文件已变化；请重新校验配置")
                if baseline is None:
                    # Exclusive creation refuses any file appearing since preview.
                    os.link(temporary, self.path)
                else:
                    os.replace(temporary, self.path)
                self._profile, self._bytes = deepcopy(profile), payload
                del self._pending[token]
            except OSError as exc:
                raise QuantStudioError(
                    f"配置未保存：{exc}。请检查服务启动时选择的保存位置"
                ) from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
