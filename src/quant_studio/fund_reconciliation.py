"""Read-only adapters for quant-fund external evidence reconciliation."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.runner import _write_json, safe_run_file
from quant_studio.runtime import configured_python
from quant_studio.settings import setting, subprocess_environment

CONFIRMATION_COLUMNS = (
    "evidence_id",
    "order_id",
    "fund_id",
    "side",
    "deal_date",
    "confirmed",
    "unit_nav",
    "shares",
    "gross",
    "fee",
    "currency",
    "source_ref",
)
RECEIPT_COLUMNS = (
    "evidence_id",
    "order_id",
    "fund_id",
    "received",
    "amount",
    "currency",
    "source_ref",
)
BATCH_CONFIRMATION_COLUMNS = (*CONFIRMATION_COLUMNS, "final")
BATCH_RECEIPT_COLUMNS = (*RECEIPT_COLUMNS, "confirmation_id")


class FundReconciliationStore:
    def __init__(self, runs_root: str | Path):
        self.runs_root = Path(runs_root).resolve()
        self.root = self.runs_root / ".reconciliations"
        self.root.mkdir(parents=True, exist_ok=True)

    def preview(
        self,
        run_id: str,
        mode: str,
        confirmations: str | Path,
        receipts: str | Path,
    ) -> dict:
        command, schema, confirmation_columns, receipt_columns = _mode(mode)
        run_dir, output, classification = self._fund_run(run_id)
        confirmation_path = self._input_path(confirmations)
        receipt_path = self._input_path(receipts)
        _check_header(confirmation_path, confirmation_columns)
        _check_header(receipt_path, receipt_columns)
        identifier = uuid.uuid4().hex
        python = configured_python("quant-fund") or sys.executable
        argv = [
            python,
            "-m",
            "quant_fund.cli",
            command,
            "--run",
            str(output),
            "--confirmations",
            str(confirmation_path),
            "--receipts",
            str(receipt_path),
        ]
        record = {
            "schema_version": "quant-studio.fund-reconciliation/v1",
            "reconciliation_id": identifier,
            "status": "previewed",
            "mode": mode,
            "expected_schema": schema,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "classification": classification,
            "confirmations": str(confirmation_path),
            "receipts": str(receipt_path),
            "confirmation_sha256": _sha256(confirmation_path),
            "receipt_sha256": _sha256(receipt_path),
            "run_fingerprints": _run_fingerprints(output),
            "argv": argv,
            "message": "输入路径、CSV表头和摘要已锁定；尚未调用原生对账。",
        }
        self._write(record)
        return record

    def execute(self, reconciliation_id: str, *, timeout: float = 120) -> dict:
        record = self.get(reconciliation_id)
        if record.get("status") != "previewed":
            raise QuantStudioError("只能显式执行尚未执行的对账预览")
        confirmations = self._input_path(record["confirmations"])
        receipts = self._input_path(record["receipts"])
        if (
            _sha256(confirmations) != record["confirmation_sha256"]
            or _sha256(receipts) != record["receipt_sha256"]
        ):
            raise QuantStudioError("CSV输入在预览后已改变，请重新预览")
        run_dir, output, classification = self._fund_run(record["run_id"])
        if (
            str(run_dir) != record["run_dir"]
            or classification != record["classification"]
        ):
            raise QuantStudioError("基金运行身份在预览后已改变，请重新预览")
        if _run_fingerprints(output) != record["run_fingerprints"]:
            raise QuantStudioError("基金运行证据在预览后已改变，请重新预览")
        workspace = setting("QUANT_WORKSPACE_ROOT")
        if not workspace:
            raise QuantStudioError("未设置 QUANT_WORKSPACE_ROOT，无法调用 quant-fund")
        cwd = (Path(workspace).resolve() / "quant-fund").resolve()
        if not cwd.is_dir():
            raise QuantStudioError("工作区里没有 quant-fund")
        record.update(status="running", message="正在执行原生只读对账")
        self._write(record)
        try:
            completed = subprocess.run(
                record["argv"],
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
            self._log(reconciliation_id, completed.stdout, completed.stderr)
            if completed.returncode not in {0, 2}:
                raise QuantStudioError(
                    completed.stderr.strip()[-1600:] or "原生对账命令失败"
                )
            report = json.loads(completed.stdout)
            _validate_report(
                report,
                expected_schema=record["expected_schema"],
                classification=classification,
                returncode=completed.returncode,
            )
            if _run_fingerprints(output) != record["run_fingerprints"]:
                raise QuantStudioError("原生对账期间基金运行证据发生改变")
            record.update(
                status=report["status"],
                message="原生只读对账完成；未修改账户现金或基金运行。",
                report=report,
                returncode=completed.returncode,
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            subprocess.SubprocessError,
            QuantStudioError,
        ) as exc:
            record.update(status="failed", message=f"对账失败：{exc}")
        self._write(record)
        return record

    def get(self, reconciliation_id: str) -> dict:
        path = self._path(reconciliation_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise QuantStudioError("对账记录不存在") from exc
        except (OSError, ValueError) as exc:
            raise QuantStudioError(f"对账记录损坏：{exc}") from exc
        if (
            not isinstance(record, dict)
            or record.get("reconciliation_id") != reconciliation_id
        ):
            raise QuantStudioError("对账记录损坏")
        return record

    def _fund_run(self, run_id: str) -> tuple[Path, Path, str]:
        run_dir = safe_run_file(self.runs_root, run_id)
        request = _json_object(safe_run_file(run_dir, "request.json"), "运行请求")
        result = _json_object(safe_run_file(run_dir, "result.json"), "运行结果")
        if request.get("template_id") != "fund-fof":
            raise QuantStudioError("只能对基金与FOF运行进行对账")
        if result.get("status") != "succeeded":
            raise QuantStudioError("基金运行尚未成功完成")
        output = safe_run_file(run_dir, "strategy-output")
        manifest = _json_object(
            safe_run_file(output, "manifest.json", {".json"}), "基金运行manifest"
        )
        classification = manifest.get("classification")
        if classification not in {"synthetic", "public_source", "user_provided"}:
            raise QuantStudioError("基金运行的数据性质声明无效")
        return run_dir, output, classification

    def _input_path(self, raw: str | Path) -> Path:
        supplied = Path(raw).expanduser()
        if not supplied.is_absolute():
            raise QuantStudioError("对账CSV必须使用绝对路径")
        if supplied.is_symlink() or (
            hasattr(supplied, "is_junction") and supplied.is_junction()
        ):
            raise QuantStudioError(
                "对账输入须为独立CSV文件，不能使用符号链接或目录联接"
            )
        path = supplied.resolve()
        allowed_value = setting("QUANT_FUND_RECONCILE_ROOT")
        allowed = Path(allowed_value).resolve() if allowed_value else self.runs_root
        try:
            path.relative_to(allowed)
        except ValueError as exc:
            raise QuantStudioError(f"对账CSV须位于允许目录：{allowed}") from exc
        if path.suffix.lower() != ".csv" or not path.is_file():
            raise QuantStudioError("对账输入须为允许目录内已有的独立CSV文件")
        return path

    def _path(self, reconciliation_id: str) -> Path:
        if len(reconciliation_id) != 32 or any(
            char not in "0123456789abcdef" for char in reconciliation_id
        ):
            raise QuantStudioError("非法对账记录 id")
        return self.root / f"{reconciliation_id}.json"

    def _write(self, record: dict) -> None:
        _write_json(self._path(record["reconciliation_id"]), record)

    def _log(self, identifier: str, stdout: str, stderr: str) -> None:
        (self.root / f"{identifier}.stdout.txt").write_text(stdout, encoding="utf-8")
        (self.root / f"{identifier}.stderr.txt").write_text(stderr, encoding="utf-8")


def _mode(mode: str):
    if mode == "batches":
        return (
            "reconcile-batches",
            "quant-fund.batch-reconciliation/v1",
            BATCH_CONFIRMATION_COLUMNS,
            BATCH_RECEIPT_COLUMNS,
        )
    if mode == "observations":
        return (
            "reconcile-observations",
            "quant-fund.observation-reconciliation/v1",
            CONFIRMATION_COLUMNS,
            RECEIPT_COLUMNS,
        )
    raise QuantStudioError("未知基金对账模式")


def _check_header(path: Path, expected: tuple[str, ...]) -> None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            fields = csv.reader(stream).__next__()
    except (OSError, UnicodeError, StopIteration) as exc:
        raise QuantStudioError(f"无法读取CSV表头：{exc}") from exc
    if len(fields) != len(set(fields)) or set(fields) != set(expected):
        raise QuantStudioError("CSV表头必须唯一且与所选原生对账契约完全一致")


def _validate_report(
    report: object, *, expected_schema: str, classification: str, returncode: int
) -> None:
    if not isinstance(report, dict) or report.get("schema") != expected_schema:
        raise QuantStudioError("原生对账输出契约不匹配")
    if (
        report.get("read_only") is not True
        or report.get("real_business_certified") is not False
    ):
        raise QuantStudioError("原生对账没有保持只读真实性边界")
    if report.get("classification") != classification:
        raise QuantStudioError("原生对账改变了基金运行的数据性质")
    status = report.get("status")
    allowed = {"matched", "differences", "pending", "no_trades"}
    if status not in allowed:
        raise QuantStudioError("原生对账状态无效")
    if (status == "matched") != (returncode == 0):
        raise QuantStudioError("原生对账状态与退出码不一致")
    for field in ("differences",):
        if not isinstance(report.get(field), list):
            raise QuantStudioError(f"原生对账缺少{field}")
    if expected_schema.endswith("batch-reconciliation/v1") and not isinstance(
        report.get("cash_by_confirmation"), list
    ):
        raise QuantStudioError("原生批次对账缺少逐确认收款状态")


def _json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuantStudioError(f"{label}无法读取：{exc}") from exc
    if not isinstance(value, dict):
        raise QuantStudioError(f"{label}结构无效")
    return value


def _run_fingerprints(output: Path) -> dict[str, str]:
    result = {}
    for name in ("manifest.json", "trades.csv", "nav.csv"):
        path = safe_run_file(output, name)
        if path.is_file():
            result[name] = _sha256(path)
    if "manifest.json" not in result:
        raise QuantStudioError("基金运行缺少manifest.json")
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
