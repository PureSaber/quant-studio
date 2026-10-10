"""Explicit, finite research metrics and comparisons with visible conditions."""

import csv
import io
import json
import math

from quant_studio import QuantStudioError
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore, digest


def validate_metrics(value):
    if not isinstance(value, dict) or len(value) > 100:
        raise QuantStudioError("research_metrics须为对象，最多100项指标")
    for name, metric in value.items():
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 120
            or not isinstance(metric, dict)
        ):
            raise QuantStudioError("指标名称或定义无效")
        if not {"value", "unit", "definition"} <= set(metric) or set(metric) - {
            "value",
            "unit",
            "definition",
            "reason",
        }:
            raise QuantStudioError("每项指标须声明value、unit和definition")
        for key, limit in (("unit", 80), ("definition", 1000)):
            if (
                not isinstance(metric[key], str)
                or not 1 <= len(metric[key].strip()) <= limit
            ):
                raise QuantStudioError("指标单位与统计口径必须明确")
        if "reason" in metric and (
            not isinstance(metric["reason"], str) or len(metric["reason"]) > 1000
        ):
            raise QuantStudioError("指标缺失原因无效")
        number = metric["value"]
        if number is None:
            if not metric.get("reason", "").strip():
                raise QuantStudioError("缺失指标须说明reason，不可用0代替")
        else:
            try:
                valid = type(number) in (int, float) and math.isfinite(number)
            except OverflowError:
                valid = False
            if not valid:
                raise QuantStudioError("指标须为有限数值或有说明的null")
    return value


def load_metrics(path):
    if path.is_symlink() or path.stat().st_size > 200_000:
        raise QuantStudioError("指标文件无效或超过200KB")
    try:
        return validate_metrics(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError, OverflowError) as exc:
        raise QuantStudioError("指标文件无法读取") from exc


def compare_notebooks(root, references):
    if not 2 <= len(references) <= 4 or len(set(references)) != len(references):
        raise QuantStudioError("请选择2至4个不同的Notebook实验")
    store = NotebookStore(root)
    records = []
    for reference in references:
        parts = reference.split(":")
        if len(parts) != 2:
            raise QuantStudioError("实验引用无效")
        project_id, run_id = parts
        record = store.get(project_id, run_id)
        project = ProjectStore(root).get(project_id, record["project_revision"])
        context = json.loads(
            store.artifact(project_id, run_id, "inputs.json").read_text(
                encoding="utf-8"
            )
        )
        metrics = {}
        if record["status"] == "succeeded" and "metrics.json" in record.get(
            "artifacts", {}
        ):
            metrics = load_metrics(store.artifact(project_id, run_id, "metrics.json"))
        environment = record["hashes"].get("requested-environment.json", "未记录")
        # Do not let executable code drift hide behind the name of a saved template.
        code = record["hashes"]["source.ipynb"]
        datasets = [
            {k: d.get(k) for k in ("id", "identity", "scope")}
            for d in context["datasets"]
        ]
        records.append(
            {
                "reference": reference,
                "project_id": project_id,
                "id": run_id,
                "name": project["name"],
                "status": record["status"],
                "context": context,
                "metrics": metrics,
                "code": code,
                "environment": environment,
                "data_signature": digest(datasets),
                "created_at": record["created_at"],
            }
        )
    warnings = []
    for key, message in (
        ("code", "执行代码不同"),
        ("environment", "运行环境不同"),
        ("data_signature", "数据版本或登记范围不同"),
    ):
        if len({r[key] for r in records}) > 1:
            warnings.append(message)
    if len({digest(r["context"].get("parameters", {})) for r in records}) > 1:
        warnings.append("实验参数不同，请结合参数变化解读指标")
    if any(r["status"] != "succeeded" for r in records):
        warnings.append("未成功实验的指标不参与比较")
    keys = sorted(
        {
            (name, m["unit"], m["definition"])
            for r in records
            for name, m in r["metrics"].items()
        }
    )
    if len({k[0] for k in keys}) != len(keys):
        warnings.append("同名指标存在不同单位或统计口径，已分行展示")
    rows = []
    for name, unit, definition in keys:
        values = []
        for record in records:
            m = record["metrics"].get(name)
            if m is None or (m["unit"], m["definition"]) != (unit, definition):
                values.append(
                    "未提供此口径"
                    if record["status"] == "succeeded"
                    else "未成功，不采用"
                )
            elif m["value"] is None:
                values.append("缺失：" + m["reason"])
            else:
                values.append(m["value"])
        rows.append([name, unit, definition, *values])
    return {"records": records, "warnings": warnings, "rows": rows}


def condition_rows(comparison, *, compact=False):
    records = comparison["records"]

    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    if compact:
        templates = [r["context"].get("template") for r in records]
        return [
            [
                "状态",
                *[
                    {
                        "succeeded": "已完成",
                        "failed": "失败",
                        "cancelled": "已取消",
                        "blocked": "准备失败",
                        "running": "运行中",
                        "queued": "排队中",
                        "ready": "待运行",
                        "preparing": "准备中",
                    }.get(r["status"], r["status"])
                    for r in records
                ],
            ],
            ["参数", *[encoded(r["context"].get("parameters", {})) for r in records]],
            [
                "模板",
                *[
                    t["name"] + " · " + t["revision"][:12] if t else "未使用个人模板"
                    for t in templates
                ],
            ],
            [
                "数据范围",
                *[
                    "；".join(
                        d["name"] + "：" + scope_description(d.get("scope"))
                        for d in r["context"]["datasets"]
                    )
                    or "未连接登记数据"
                    for r in records
                ],
            ],
        ]
    return [
        ["状态", *[r["status"] for r in records]],
        ["参数", *[encoded(r["context"].get("parameters", {})) for r in records]],
        [
            "模板版本",
            *[encoded(r["context"].get("template", "未使用个人模板")) for r in records],
        ],
        [
            "数据与登记范围",
            *[
                encoded(
                    [
                        {k: d.get(k, "未声明") for k in ("name", "identity", "scope")}
                        for d in r["context"]["datasets"]
                    ]
                )
                for r in records
            ],
        ],
        ["执行代码SHA-256", *[r["code"] for r in records]],
        ["环境SHA-256", *[r["environment"] for r in records]],
        ["提交时间", *[r["created_at"] for r in records]],
    ]


def scope_description(scope):
    if not isinstance(scope, dict):
        return "范围未登记"
    start, end = scope.get("start"), scope.get("end")
    dates = f"{start or '未登记'}至{end or '未登记'}"
    symbols = scope.get("symbols")
    names = "、".join(str(s) for s in symbols) if symbols else "标的未登记或未限定"
    return dates + "；" + names


def comparison_csv(comparison):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)

    def write(row):
        # Only protect textual cells; genuine negative numeric metrics stay numeric.
        writer.writerow(
            [
                "'" + v
                if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                else v
                for v in row
            ]
        )

    names = [r["name"] + " · " + r["id"][:8] for r in comparison["records"]]
    write(["实验条件", *names])
    for row in condition_rows(comparison):
        write(row)
    write([])
    for warning in comparison["warnings"]:
        write(["比较提示", warning])
    write(["指标", "单位", "统计口径", *names])
    for row in comparison["rows"]:
        write(row)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")
