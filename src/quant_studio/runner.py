from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from quant_studio import QuantStudioError
from quant_studio.templates import Template, load_template, render_template


@dataclass
class RunResult:
    status: str
    run_id: str
    run_dir: Path
    argv: list[str]
    report: str | None = None
    returncode: int | None = None
    message: str | None = None

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["run_dir"] = str(self.run_dir)
        return data


def preview(
    template: str | Template,
    knobs: dict[str, Any] | None = None,
    *,
    runs_root: str | Path | None = None,
    run_id: str | None = None,
) -> RunResult:
    loaded = load_template(template) if isinstance(template, str) else template
    rendered = render_template(loaded, knobs)
    identifier, run_dir = _create_run_dir(runs_root, run_id)
    config_path = run_dir / f"config.{loaded.config_format}"
    _write_config(config_path, loaded.config_format, rendered.config)
    argv = _render_argv(loaded.argv, rendered.values, run_dir, config_path)
    _write_json(
        run_dir / "request.json",
        {"template_id": loaded.id, "knobs": knobs or {}},
    )
    _write_json(run_dir / "command.json", {"argv": argv})
    result = RunResult("previewed", identifier, run_dir, argv)
    _write_json(run_dir / "result.json", result.as_json())
    return result


def run(
    template: str | Template,
    knobs: dict[str, Any] | None = None,
    *,
    execute: bool = False,
    runs_root: str | Path | None = None,
    run_id: str | None = None,
    timeout: float = 300,
) -> RunResult:
    loaded = load_template(template) if isinstance(template, str) else template
    if not execute:
        return preview(loaded, knobs, runs_root=runs_root, run_id=run_id)

    rendered = render_template(loaded, knobs)
    identifier, run_dir = _create_run_dir(runs_root, run_id)
    config_path = run_dir / f"config.{loaded.config_format}"
    _write_config(config_path, loaded.config_format, rendered.config)
    argv = _render_argv(loaded.argv, rendered.values, run_dir, config_path)
    _write_json(
        run_dir / "request.json",
        {"template_id": loaded.id, "knobs": knobs or {}, "execute": True},
    )
    _write_json(run_dir / "command.json", {"argv": argv})

    if loaded.kind == "synthetic":
        from quant_studio.synthetic import execute_synthetic

        execute_synthetic(loaded, rendered, run_dir)
        result = RunResult(
            "succeeded", identifier, run_dir, argv, loaded.report_name, 0
        )
        _write_json(run_dir / "result.json", result.as_json())
        return result

    report_path = safe_run_file(run_dir, loaded.report_name, {".html"})
    workspace_value = os.environ.get("QUANT_WORKSPACE_ROOT")
    if not workspace_value:
        result = RunResult(
            "blocked",
            identifier,
            run_dir,
            argv,
            message="QUANT_WORKSPACE_ROOT 未设置",
        )
        _write_json(run_dir / "result.json", result.as_json())
        return result
    workspace_root = Path(workspace_value).resolve()
    cwd = (workspace_root / loaded.workspace_repo).resolve()
    try:
        cwd.relative_to(workspace_root)
    except ValueError as exc:
        raise QuantStudioError("workspace_repo 逃出 workspace root") from exc
    if not cwd.is_dir():
        result = RunResult(
            "blocked",
            identifier,
            run_dir,
            argv,
            message=f"上游仓库目录不存在: {cwd}",
        )
        _write_json(run_dir / "result.json", result.as_json())
        return result

    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr) + f"\n运行超时（{timeout} 秒）"
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    succeeded = returncode == 0
    result = RunResult(
        "succeeded" if succeeded else "failed",
        identifier,
        run_dir,
        argv,
        loaded.report_name if succeeded and report_path.is_file() else None,
        returncode,
    )
    _write_json(run_dir / "result.json", result.as_json())
    return result


def safe_run_file(
    run_dir: str | Path, relative_path: str, suffixes: set[str] | None = None
) -> Path:
    root = Path(run_dir).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise QuantStudioError(f"非法运行文件路径: {relative_path}") from exc
    if suffixes is not None and candidate.suffix.lower() not in suffixes:
        raise QuantStudioError(f"不允许的运行文件类型: {relative_path}")
    return candidate


def _create_run_dir(
    runs_root: str | Path | None, run_id: str | None
) -> tuple[str, Path]:
    root = (
        Path(runs_root)
        if runs_root is not None
        else Path(__file__).resolve().parents[2] / "runs"
    )
    root.mkdir(parents=True, exist_ok=True)
    if run_id is not None:
        candidate = root / run_id
        try:
            candidate.mkdir()
        except FileExistsError as exc:
            raise QuantStudioError(f"运行目录已存在: {run_id}") from exc
        return run_id, candidate.resolve()
    while True:
        identifier = uuid.uuid4().hex
        candidate = root / identifier
        try:
            candidate.mkdir()
            return identifier, candidate.resolve()
        except FileExistsError:
            continue


def _render_argv(
    argv: list[str],
    values: dict[str, Any],
    run_dir: Path,
    config_path: Path,
) -> list[str]:
    replacements = {
        **{name: str(value) for name, value in values.items()},
        "config": str(config_path),
        "snapshot": str(run_dir / "snapshot"),
        "output": str(run_dir / "strategy-output"),
    }
    try:
        return [part.format_map(replacements) for part in argv]
    except KeyError as exc:
        raise QuantStudioError(f"命令占位符无效: {exc.args[0]}") from exc


def _write_config(path: Path, config_format: str, config: dict[str, Any]) -> None:
    if config_format == "yaml":
        path.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    else:
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
