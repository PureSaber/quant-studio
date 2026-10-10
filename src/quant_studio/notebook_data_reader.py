"""Frozen Notebook bridge for immutable QDK intake snapshots.

This file intentionally has no quant-studio imports.  Notebook preparation copies the
exact bytes beside ``source.ipynb`` so the submitted source, bridge, QDK script, runtime
identity, and input bytes can be verified as one execution bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

_PURPOSES = {
    "exploration",
    "daily_bars_research",
    "historical_financial_factor_backtest",
}
_SCOPE_KEYS = ("columns", "symbols", "start", "end")


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _safe_environment() -> dict[str, str]:
    allowed = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "COMSPEC",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed
    }
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return environment


def probe_python_environment(python: str | Path) -> dict:
    """Read the independent interpreter identity without importing project code."""
    executable = Path(python)
    if not executable.is_absolute() or not executable.is_file():
        raise RuntimeError("QDK解释器必须是存在的绝对文件")
    program = (
        "import importlib.metadata as m,json,platform,sys;"
        "names=('numpy','pandas','pyarrow');"
        "versions={n:(m.version(n) if any(d.metadata.get('Name','').lower()==n "
        "for d in m.distributions()) else None) for n in names};"
        "print(json.dumps({'python':sys.executable,'version':platform.python_version(),"
        "'implementation':platform.python_implementation(),'packages':versions},sort_keys=True))"
    )
    try:
        result = subprocess.run(
            [str(executable), "-I", "-X", "utf8", "-c", program],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=_safe_environment(),
        )
        if result.returncode:
            raise RuntimeError("QDK解释器身份读取失败：" + result.stderr[-1000:])
        value = json.loads(result.stdout)
        if (
            not isinstance(value, dict)
            or set(value) != {"python", "version", "implementation", "packages"}
            or set(value["packages"]) != {"numpy", "pandas", "pyarrow"}
        ):
            raise ValueError("invalid environment response")
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        raise RuntimeError("无法核验QDK解释器身份") from exc


def snapshot_hashes(snapshot: str | Path) -> dict[str, str]:
    """Hash the exact regular-file set and reject links at every level."""
    root = Path(snapshot)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("固定数据版本目录不存在或为链接")
    root = root.resolve()
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("固定数据版本不可包含链接")
        if path.is_dir():
            continue
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise RuntimeError("固定数据版本含有越界或非普通文件")
        hashes[path.relative_to(root).as_posix()] = _file_hash(path)
    if not hashes:
        raise RuntimeError("固定数据版本为空")
    return hashes


def _resolved(base: Path, value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        if ".." in path.parts:
            raise RuntimeError(f"{label}路径越界")
        path = base / path
        resolved = path.resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise RuntimeError(f"{label}路径越界")
        return resolved
    return path.resolve()


def _validated_intake(dataset: dict) -> dict:
    if not isinstance(dataset, dict) or dataset.get("kind") != "intake":
        raise RuntimeError("只允许通过固定读取器读取登记kind=intake的数据版本")
    intake = dataset.get("intake")
    if not isinstance(intake, dict) or set(intake) != {
        "name",
        "version",
        "purpose",
        "scope",
        "check",
    }:
        raise RuntimeError("数据登记缺少固定用途、范围或准入结果")
    if intake["purpose"] not in _PURPOSES:
        raise RuntimeError("数据登记用途无效")
    scope = intake["scope"]
    if not isinstance(scope, dict) or not set(scope) <= set(_SCOPE_KEYS):
        raise RuntimeError("数据登记范围无效")
    for key in ("columns", "symbols"):
        value = scope.get(key)
        if value is not None and (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise RuntimeError(f"固定{key}范围无效")
    for key in ("start", "end"):
        value = scope.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise RuntimeError(f"固定{key}范围无效")
    check = intake["check"]
    if (
        not isinstance(check, dict)
        or check.get("allowed") is not True
        or check.get("status") != "ready"
        or check.get("purpose") != intake["purpose"]
        or check.get("version_id") != intake["version"]
        or not isinstance(check.get("scope"), dict)
        or any(check["scope"].get(key) != value for key, value in scope.items())
    ):
        raise RuntimeError("登记用途或范围没有可复现的准入结果")
    hashes = dataset.get("hashes")
    if (
        not isinstance(hashes, dict)
        or not hashes
        or not all(
            isinstance(key, str) and key and isinstance(value, str) and len(value) == 64
            for key, value in hashes.items()
        )
    ):
        raise RuntimeError("数据登记缺少完整文件哈希")
    return intake


def read_intake_dataset(
    dataset: dict, runtime: dict, *, base: str | Path = "."
) -> dict:
    """Run the frozen QDK ``read-snapshot`` command with the registered scope."""
    intake = _validated_intake(dataset)
    base_path = Path(base).resolve()
    snapshot = _resolved(base_path, dataset["path"], "数据版本")
    script = _resolved(base_path, runtime.get("script", ""), "QDK读取器")
    python = runtime.get("python")
    if not isinstance(python, str):
        raise RuntimeError("QDK解释器身份缺失")
    expected_hashes = dataset["hashes"]
    before_hashes = snapshot_hashes(snapshot)
    if before_hashes != expected_hashes:
        raise RuntimeError("固定数据版本完整性失败")
    if script.is_symlink() or not script.is_file():
        raise RuntimeError("冻结QDK读取器不存在或为链接")
    script_hash = _file_hash(script)
    if script_hash != runtime.get("script_sha256"):
        raise RuntimeError("冻结QDK读取器完整性失败")
    environment = probe_python_environment(python)
    if environment != runtime.get("environment"):
        raise RuntimeError("QDK解释器环境与提交时身份不同")

    scope = intake["scope"]
    command = [
        python,
        "-I",
        "-X",
        "utf8",
        str(script),
        "read-snapshot",
        "--snapshot",
        str(snapshot),
        "--purpose",
        intake["purpose"],
    ]
    for key in _SCOPE_KEYS:
        value = scope.get(key)
        if value:
            command += ["--" + key, *(value if isinstance(value, list) else [value])]
    command += ["--limit", "0"]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            env=_safe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("QDK固定版本读取失败") from exc

    if snapshot_hashes(snapshot) != before_hashes:
        raise RuntimeError("固定数据版本在读取期间发生变化")
    if _file_hash(script) != script_hash:
        raise RuntimeError("冻结QDK读取器在读取期间发生变化")
    if probe_python_environment(python) != environment:
        raise RuntimeError("QDK解释器环境在读取期间发生变化")
    try:
        response = json.loads(result.stdout)
        data = response["data"]
        valid = (
            result.returncode == 0
            and response["schema_version"] == "qdk.research-intake-response/v1"
            and response["ok"] is True
            and response["action"] == "read-snapshot"
            and data["version_id"] == intake["version"]
            and data["check"] == intake["check"]
            and data["complete"] is True
            and data["truncated"] is False
            and data["returned_rows"] == data["matched_rows"]
            and isinstance(data["rows"], list)
        )
        if not valid:
            raise ValueError("response mismatch")
        return data
    except (KeyError, TypeError, ValueError) as exc:
        detail = result.stderr[-1000:] if result.stderr else "返回契约与固定登记不一致"
        raise RuntimeError("QDK固定版本读取回执无效：" + detail) from exc


__all__ = ["probe_python_environment", "read_intake_dataset", "snapshot_hashes"]
