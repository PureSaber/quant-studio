from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from quant_studio import QuantStudioError
from quant_studio.flow import format_argv
from quant_studio.nav import collect_outputs
from quant_studio.runtime import configured_python
from quant_studio.settings import current_settings, setting, subprocess_environment
from quant_studio.templates import (
    Template,
    apply_factor_selection,
    load_template,
    render_template,
)


@dataclass(frozen=True)
class Readiness:
    runnable: bool
    message: str
    needs_input: bool = False
    executable: str | None = None


def template_readiness(
    template: Template, *, snapshot: str | Path | None = None
) -> Readiness:
    if template.kind == "synthetic":
        return Readiness(True, "可运行")
    repo = str(template.workspace_repo)
    root = setting("QUANT_WORKSPACE_ROOT")
    if not root:
        return Readiness(False, f"未设置 QUANT_WORKSPACE_ROOT，{repo} 只能预览")
    if not (Path(root) / repo).is_dir():
        return Readiness(False, f"工作区里没有 {repo}，只能预览")
    try:
        python = configured_python(template.workspace_repo)
    except QuantStudioError as exc:
        return Readiness(False, str(exc))
    executable = _executable(template.argv[0], python=python)
    if executable is None:
        return Readiness(False, f"找不到命令 {template.argv[0]}，请安装对应环境后运行")
    if template.argv[0] in {"python", "python3", sys.executable} and template.argv[
        1:2
    ] == ["-m"]:
        module = template.argv[2]
        try:
            if python is None:
                available = importlib.util.find_spec(module) is not None
            else:
                check = subprocess.run(
                    [
                        executable,
                        "-c",
                        "import importlib.util,sys; "
                        "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)",
                        module,
                    ],
                    capture_output=True,
                    env=subprocess_environment(),
                    timeout=10,
                    check=False,
                )
                available = check.returncode == 0
        except (ImportError, ValueError, OSError, subprocess.SubprocessError):
            available = False
        if not available:
            return Readiness(
                False,
                f"所选Python无法加载模块{module}：{executable}",
                executable=executable,
            )
    if template.metadata.get("requires_snapshot"):
        selected = _snapshot_path(template, snapshot)
        source = template.metadata["input_source"]
        kind = source.get("kind", "directory")
        if kind == "file":
            valid = selected is not None and selected.is_file()
            requirement = "（需选择已有文件）"
        elif kind == "directory":
            valid = (
                selected is not None
                and selected.is_dir()
                and all(
                    (selected / name).is_file() for name in source["required_files"]
                )
            )
            requirement = f"（需包含{', '.join(source['required_files'])}）"
        else:
            raise QuantStudioError(f"未知输入来源类型：{kind}")
        if not valid:
            return Readiness(
                False,
                f"请选择已有{source['label']}{requirement}",
                True,
                executable,
            )
    return Readiness(True, "可运行", executable=executable)


@dataclass
class RunResult:
    status: str
    run_id: str
    run_dir: Path
    argv: list[str]
    report: str | None = None
    returncode: int | None = None
    message: str | None = None
    evidence_kind: str | None = None
    research_view_sha256: str | None = None

    def as_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["run_dir"] = str(self.run_dir)
        return data


def preview(
    template: str | Template,
    knobs: dict[str, Any] | None = None,
    *,
    factors: list[str] | None = None,
    runs_root: str | Path | None = None,
    run_id: str | None = None,
    snapshot: str | Path | None = None,
) -> RunResult:
    loaded = load_template(template) if isinstance(template, str) else template
    rendered = render_template(loaded, knobs)
    if factors is not None:
        apply_factor_selection(rendered.config, loaded, factors)
    identifier, run_dir = _create_run_dir(runs_root, run_id)
    _isolate_outputs(loaded, rendered.config, run_dir)
    _resolve_input_paths(loaded, rendered.config)
    selected_snapshot = _snapshot_path(loaded, snapshot)
    config_path = run_dir / f"config.{loaded.config_format}"
    _write_config(config_path, loaded.config_format, rendered.config)
    argv = _render_argv(
        loaded.argv,
        rendered.values,
        run_dir,
        config_path,
        selected_snapshot,
        python=configured_python(loaded.metadata.get("workspace_repo")),
    )
    _write_json(
        run_dir / "request.json",
        {
            "template_id": loaded.id,
            "knobs": knobs or {},
            "factors": factors,
            "snapshot": str(selected_snapshot) if selected_snapshot else None,
        },
    )
    _write_json(run_dir / "command.json", {"argv": argv})
    result = RunResult("previewed", identifier, run_dir, argv)
    _write_json(run_dir / "result.json", result.as_json())
    return result


def run(
    template: str | Template,
    knobs: dict[str, Any] | None = None,
    *,
    factors: list[str] | None = None,
    execute: bool = False,
    runs_root: str | Path | None = None,
    run_id: str | None = None,
    timeout: float = 300,
    snapshot: str | Path | None = None,
) -> RunResult:
    loaded = load_template(template) if isinstance(template, str) else template
    if not execute:
        return preview(
            loaded,
            knobs,
            factors=factors,
            runs_root=runs_root,
            run_id=run_id,
            snapshot=snapshot,
        )

    rendered = render_template(loaded, knobs)
    if factors is not None:
        apply_factor_selection(rendered.config, loaded, factors)
    identifier, run_dir = _create_run_dir(runs_root, run_id)
    _isolate_outputs(loaded, rendered.config, run_dir)
    _resolve_input_paths(loaded, rendered.config)
    selected_snapshot = _snapshot_path(loaded, snapshot)
    config_path = run_dir / f"config.{loaded.config_format}"
    _write_config(config_path, loaded.config_format, rendered.config)
    argv = _render_argv(
        loaded.argv,
        rendered.values,
        run_dir,
        config_path,
        selected_snapshot,
        python=configured_python(loaded.metadata.get("workspace_repo")),
    )
    _write_json(
        run_dir / "request.json",
        {
            "template_id": loaded.id,
            "knobs": knobs or {},
            "factors": factors,
            "execute": True,
            "snapshot": str(selected_snapshot) if selected_snapshot else None,
        },
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

    safe_run_file(run_dir, loaded.report_name, {".html"})
    workspace_value = setting("QUANT_WORKSPACE_ROOT")
    if workspace_value:
        workspace_root = Path(workspace_value).resolve()
        cwd = (workspace_root / loaded.workspace_repo).resolve()
        try:
            cwd.relative_to(workspace_root)
        except ValueError as exc:
            raise QuantStudioError("workspace_repo 逃出 workspace root") from exc
    readiness = template_readiness(loaded, snapshot=selected_snapshot)
    if not readiness.runnable:
        result = RunResult(
            "blocked",
            identifier,
            run_dir,
            argv,
            message=readiness.message,
        )
        _write_json(run_dir / "result.json", result.as_json())
        return result
    if argv[0] != readiness.executable:
        raise QuantStudioError("运行环境配置在预检期间改变，请重新预览")
    workspace_root = Path(setting("QUANT_WORKSPACE_ROOT")).resolve()
    cwd = (workspace_root / loaded.workspace_repo).resolve()

    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=subprocess_environment(),
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
    except OSError as exc:
        returncode = None
        stdout = ""
        stderr = f"无法启动命令: {exc}"
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    succeeded = returncode == 0
    message = None
    evidence_kind = None
    research_view_sha256 = None
    report_name = loaded.report_name
    family_report_verified = False
    if loaded.metadata.get("counterfactual_view"):
        try:
            if returncode not in {0, 2}:
                raise QuantStudioError("原生候选族未正常完成，请查看执行日志")
            _project_standard_output(loaded, run_dir, argv, cwd, timeout)
            view_path = safe_run_file(run_dir, "strategy-output/studio-view/view.json")
            view = json.loads(view_path.read_text(encoding="utf-8"))
            if view["native_exit_code"] != returncode:
                raise QuantStudioError("原生退出状态与候选族证据不一致")
            report_name = collect_outputs(
                cwd,
                run_dir,
                _output_dirs(loaded, rendered.config),
                result_files=loaded.metadata["result_files"],
            )
            evidence_kind = _evidence_kind(loaded, view)
            research_view_sha256 = hashlib.sha256(view_path.read_bytes()).hexdigest()
            family_report_verified = True
            if returncode == 2:
                message = "候选族未完成，保留失败证据；效应与联合交互结论不可用。"
        except (OSError, ValueError, KeyError, TypeError, QuantStudioError) as exc:
            succeeded = False
            message = f"无法核验本次择时反事实: {exc}"
    if loaded.metadata.get("timing_view") and returncode in {0, 1, 2}:
        manifest = safe_run_file(run_dir, "strategy-output/standard/run_manifest.json")
        if manifest.is_file():
            try:
                _project_standard_output(loaded, run_dir, argv, cwd, timeout)
                view_path = safe_run_file(
                    run_dir, "strategy-output/studio-view/view.json"
                )
                view = json.loads(view_path.read_text(encoding="utf-8"))
                if view["native_exit_code"] != returncode:
                    raise QuantStudioError("原生退出状态与研究证据不一致")
                evidence_kind = _evidence_kind(loaded, view)
                research_view_sha256 = hashlib.sha256(
                    view_path.read_bytes()
                ).hexdigest()
            except (OSError, ValueError, KeyError, TypeError, QuantStudioError) as exc:
                succeeded = False
                message = f"无法核验本次择时研究: {exc}"
    if succeeded and not family_report_verified:
        try:
            if loaded.metadata.get("standard_view"):
                _project_standard_output(loaded, run_dir, argv, cwd, timeout)
            opening_key = loaded.metadata.get("nav_initial_value_key")
            collected_report = collect_outputs(
                cwd,
                run_dir,
                _output_dirs(loaded, rendered.config),
                result_files=loaded.metadata.get("result_files"),
                nav_column=loaded.metadata.get("nav_column"),
                nav_strategy=loaded.metadata.get("nav_strategy"),
                initial_nav=(
                    rendered.config[opening_key]
                    if opening_key
                    else loaded.metadata.get("nav_initial_value")
                ),
            )
            report_name = collected_report or report_name
            evidence = loaded.metadata.get("result_evidence")
            if evidence:
                path = safe_run_file(
                    run_dir, "strategy-output/" + evidence["file"], {".json"}
                )
                evidence_kind = _evidence_kind(
                    loaded, json.loads(path.read_text(encoding="utf-8"))
                )
        except (OSError, ValueError, KeyError, TypeError, QuantStudioError) as exc:
            succeeded = False
            message = f"无法收集本次运行结果: {exc}"
    report = run_dir / report_name
    if succeeded and not report.is_file() and not (run_dir / "nav.csv").is_file():
        message = "命令已结束，但没有找到净值序列或 report.html"
        succeeded = False
    result = RunResult(
        "succeeded" if succeeded else "failed",
        identifier,
        run_dir,
        argv,
        report_name
        if (succeeded or family_report_verified) and report.is_file()
        else None,
        returncode,
        message,
        evidence_kind,
        research_view_sha256,
    )
    _write_json(run_dir / "result.json", result.as_json())
    return result


def preflight(
    template: str | Template,
    knobs: dict[str, Any] | None = None,
    *,
    factors: list[str] | None = None,
    snapshot: str | Path | None = None,
    runs_root: str | Path | None = None,
    timeout: float = 60,
) -> RunResult:
    """Run an explicitly declared upstream data check; never substitute a backtest."""
    loaded = load_template(template) if isinstance(template, str) else template
    declared = loaded.metadata.get("preflight_argv")
    if not declared:
        raise QuantStudioError("该模板尚未接入独立的数据预检，请查看模板的数据要求。")
    prepared = preview(
        loaded, knobs, factors=factors, snapshot=snapshot, runs_root=runs_root
    )
    selected = _snapshot_path(loaded, snapshot)
    ready = template_readiness(loaded, snapshot=selected)
    argv = _render_argv(
        declared,
        render_template(loaded, knobs).values,
        prepared.run_dir,
        prepared.run_dir / f"config.{loaded.config_format}",
        selected,
        python=configured_python(loaded.metadata.get("workspace_repo")),
    )
    _write_json(prepared.run_dir / "command.json", {"argv": argv})
    result = RunResult("blocked", prepared.run_id, prepared.run_dir, argv)
    result.message = ready.message
    if ready.runnable:
        if argv[0] != ready.executable:
            raise QuantStudioError("运行环境配置在预检期间改变，请重新预览")
        cwd = Path(setting("QUANT_WORKSPACE_ROOT")).resolve() / loaded.workspace_repo
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=subprocess_environment(),
                timeout=timeout,
                check=False,
            )
            (prepared.run_dir / "stdout.txt").write_text(
                completed.stdout, encoding="utf-8"
            )
            (prepared.run_dir / "stderr.txt").write_text(
                completed.stderr, encoding="utf-8"
            )
            result.returncode = completed.returncode
            if completed.returncode:
                lines = completed.stderr.strip().splitlines()
                raise QuantStudioError(lines[-1][-1600:] if lines else "上游预检失败")
            evidence = json.loads(completed.stdout)
            _validate_preflight(loaded, evidence)
            _write_json(prepared.run_dir / "preflight.json", evidence)
            result.evidence_kind = _evidence_kind(loaded, evidence)
            result.status = "checked"
            result.message = (
                "数据与配置预检通过；没有执行策略。投资适用性以原始证据为准。"
            )
        except (
            OSError,
            ValueError,
            QuantStudioError,
            subprocess.SubprocessError,
        ) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                (prepared.run_dir / "stdout.txt").write_text(
                    _text(exc.stdout), encoding="utf-8"
                )
                (prepared.run_dir / "stderr.txt").write_text(
                    _text(exc.stderr), encoding="utf-8"
                )
            result.status = "check_failed"
            result.message = f"数据预检未通过：{exc}"
    _write_json(prepared.run_dir / "result.json", result.as_json())
    return result


def _validate_preflight(template: Template, evidence: object) -> None:
    contract = template.metadata.get("preflight_contract")
    status_field = contract["status_field"] if contract else "software_preflight"
    if not isinstance(evidence, dict) or evidence.get(status_field) != "pass":
        raise QuantStudioError("上游未返回通过的数据预检证据")
    if not contract:
        return
    if (
        evidence.get("schema_version") != contract["schema_version"]
        or evidence.get("read_only") is not True
        or evidence.get("investable") is not False
    ):
        raise QuantStudioError("上游预检契约不匹配")
    _evidence_kind(template, evidence)
    for field in contract["positive_counts"]:
        if type(evidence.get(field)) is not int or evidence[field] <= 0:
            raise QuantStudioError(f"上游预检契约不匹配：{field}须为正整数")
    if contract.get("instrument_ids_required") and (
        not isinstance(evidence.get("instrument_ids"), list)
        or not evidence["instrument_ids"]
        or not all(
            isinstance(item, str) and item for item in evidence["instrument_ids"]
        )
    ):
        raise QuantStudioError("上游预检契约不匹配：缺少标的身份")


def _project_standard_output(template, run_dir, argv, cwd, timeout):
    view_kind = next(
        key
        for key in ("counterfactual_view", "timing_view", "standard_view")
        if template.metadata.get(key)
    )
    declaration = template.metadata[view_kind]
    native = safe_run_file(run_dir, "strategy-output/" + declaration["run"])
    if template.argv[:2] != ["python", "-m"]:
        raise QuantStudioError("标准账本模板必须通过明确Python模块入口运行")
    python = argv[0]
    if (
        _executable(template.argv[0], python=configured_python(template.workspace_repo))
        != python
    ):
        raise QuantStudioError("运行环境配置在执行期间改变，请重新运行")
    if not Path(python).is_file():
        raise QuantStudioError("无法定位原生核验环境的Python，请配置运行环境")
    command = [
        python,
        "-I",
        str(Path(__file__).with_name(f"{view_kind}.py")),
        "--run",
        str(native),
        "--output",
        str(run_dir / "strategy-output" / "studio-view"),
        "--project",
        template.workspace_repo,
    ]
    _write_json(run_dir / "view-command.json", {"argv": command})
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=subprocess_environment(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        (run_dir / "view-stdout.txt").write_text(_text(exc.stdout), encoding="utf-8")
        (run_dir / "view-stderr.txt").write_text(_text(exc.stderr), encoding="utf-8")
        raise QuantStudioError("原生产物核验与展示转换超时") from exc
    (run_dir / "view-stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "view-stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()
        raise QuantStudioError(
            "原生产物核验与展示转换失败："
            + (detail[-1] if detail else "请查看转换日志")
        )


def _evidence_kind(template: Template, record: dict) -> str | None:
    declaration = template.metadata.get("result_evidence")
    if not declaration:
        return None
    if not isinstance(record, dict):
        raise QuantStudioError("上游结果的数据性质声明须为JSON对象")
    value = record.get(declaration["field"])
    if value not in declaration["values"]:
        raise QuantStudioError("上游结果的数据性质声明无效")
    return value


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
    snapshot: Path | None = None,
    *,
    python: str | None = None,
) -> list[str]:
    rendered = format_argv(
        argv,
        values,
        config=str(config_path),
        snapshot=str(snapshot or run_dir / "snapshot"),
        output=str(run_dir / "strategy-output"),
    )
    if rendered:
        rendered[0] = _executable(rendered[0], python=python) or rendered[0]
    return rendered


def _executable(command: str, *, python: str | None = None) -> str | None:
    if command in {"python", "python3", sys.executable}:
        return python or sys.executable
    if python is not None:
        # A selected environment is exclusive: never borrow another environment's CLI.
        try:
            probe = subprocess.run(
                [
                    python,
                    "-I",
                    "-B",
                    "-X",
                    "utf8",
                    "-c",
                    "import json,sysconfig; "
                    "print(json.dumps(sysconfig.get_path('scripts')))",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=subprocess_environment(),
                timeout=10,
                check=False,
            )
            directory = json.loads(probe.stdout)
            if (
                probe.returncode
                or not isinstance(directory, str)
                or not Path(directory).is_absolute()
            ):
                return None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        candidate = Path(directory) / (command + (".exe" if os.name == "nt" else ""))
        return str(candidate) if candidate.is_file() else None
    search_path = (
        str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    )
    return shutil.which(command, path=search_path)


def _isolate_outputs(template: Template, config: dict[str, Any], run_dir: Path) -> None:
    if template.kind != "synthetic":
        for key in ("outputs_dir", "state_dir"):
            if key in config:
                config[key] = str(run_dir / "strategy-output")


def _snapshot_path(template: Template, snapshot: str | Path | None) -> Path | None:
    if not template.metadata.get("requires_snapshot"):
        if snapshot:
            raise QuantStudioError("该模板不接受外部数据目录")
        return None
    source = template.metadata["input_source"]
    chosen = snapshot or setting(source["environment"])
    if chosen:
        return Path(chosen).expanduser().resolve()
    profile = current_settings()
    if profile is not None and profile["environment"].get("QUANT_WORKSPACE_ROOT"):
        return None
    root = setting("QUANT_WORKSPACE_ROOT")
    if root and source.get("default"):
        return (Path(root) / template.workspace_repo / source["default"]).resolve()
    return None


def _resolve_input_paths(template: Template, config: dict[str, Any]) -> None:
    workspace = setting("QUANT_WORKSPACE_ROOT")
    fields = template.metadata.get("input_paths", [])
    if not workspace or not fields:
        return
    repo = Path(workspace).resolve() / template.workspace_repo
    for field in fields:
        parts = field.split(".")
        node = config
        for part in parts[:-1]:
            node = node[part]
        source = Path(node[parts[-1]])
        node[parts[-1]] = str((repo / source).resolve())


def _output_dirs(template: Template, config: dict[str, Any]) -> list[str]:
    dirs = [str(item) for item in template.metadata.get("output_dirs", [])]
    for key in ("outputs_dir", "state_dir"):
        value = config.get(key)
        if isinstance(value, str) and value not in dirs:
            dirs.append(value)
    return dirs


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
