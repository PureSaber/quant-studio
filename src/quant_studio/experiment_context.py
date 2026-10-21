"""Dates, immutable version bindings and readable differences for saved experiments."""

import json
from datetime import UTC, datetime
from html import escape

from quant_studio.experiments import ExperimentStore
from quant_studio.presentation import status_label
from quant_studio.run_record import load_run_result


def context(root, identifier):
    directory = ExperimentStore(root)._run(identifier)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    result = load_run_result(directory)
    path = directory / "provenance.json"
    provenance = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    recipe = request.get("recipe") or {}
    return {
        "status": result["status"],
        "request_saved_at": datetime.fromtimestamp(
            (directory / "request.json").stat().st_mtime, UTC
        ).isoformat(),
        "result_saved_at": datetime.fromtimestamp(
            (directory / "result.json").stat().st_mtime, UTC
        ).isoformat()
        if (directory / "result.json").is_file()
        else None,
        "project": request.get("research_project"),
        "recipe_revision": recipe.get("revision"),
        "recipe_created_at": recipe.get("created_at"),
        "code": provenance.get("code"),
        "input": provenance.get("input"),
        "configuration": recipe
        or {"config": request.get("knobs"), "snapshot": request.get("snapshot")},
        "failure": result.get("message") or result.get("error") or "",
    }


def context_body(root, identifier):
    from quant_studio.etf_daily_web import details, local_time
    from quant_studio.project_web import table

    value = context(root, identifier)
    body = table(
        ["实验元数据", "记录"],
        [
            ["状态", status_label(value["status"])],
            ["请求文件保存时间", local_time(value["request_saved_at"])],
            ["结果文件保存时间", local_time(value["result_saved_at"])],
            ["方案版本", value["recipe_revision"] or "历史实验未记录"],
            ["方案保存时间", local_time(value["recipe_created_at"])],
            ["项目版本", (value["project"] or {}).get("revision", "历史实验未绑定")],
        ],
    )
    body += "<p>文件时间用于定位保存记录，不替代原生运行事件时间。</p>"
    if value["failure"]:
        body += f"<p>诊断：{escape(value['failure'])}</p>"
    return body + details(
        {"code": value["code"], "input": value["input"]},
        "运行代码与输入身份（未记录时不推断）",
    )


def differences(before, after):
    """Report additions and removals in inputs and candidate parameters."""
    rows = []

    def walk(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                if key not in left:
                    rows.append(
                        [
                            f"{path}.{key}",
                            "未设置",
                            json.dumps(right[key], ensure_ascii=False),
                        ]
                    )
                elif key not in right:
                    rows.append(
                        [
                            f"{path}.{key}",
                            json.dumps(left[key], ensure_ascii=False),
                            "已移除",
                        ]
                    )
                else:
                    walk(left[key], right[key], f"{path}.{key}")
        elif left != right:
            rows.append(
                [
                    path,
                    json.dumps(left, ensure_ascii=False),
                    json.dumps(right, ensure_ascii=False),
                ]
            )

    walk(before, after, "配置")
    return rows
