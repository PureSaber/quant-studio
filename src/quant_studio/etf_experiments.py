"""Read native ETF families, retaining failures and original metrics."""

import json
from html import escape

from quant_studio import QuantStudioError
from quant_studio.experiment_context import context_body, differences, status_label
from quant_studio.experiments import ExperimentStore
from quant_studio.result_integrity import verify_result
from quant_studio.run_record import load_run_result


def read_family(root, identifier):
    directory = ExperimentStore(root)._run(identifier)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    if request["template_id"] != "etf-weekly-research":
        raise QuantStudioError("所选实验并非四ETF原生研究")
    result = load_run_result(directory)
    if result["status"] != "succeeded":
        return {
            "status": result["status"],
            "results": [],
            "error": result.get("message") or "实验失败或尚未完成",
        }
    verify_result(directory, result, asset="strategy-output/studio-view.json")
    value = json.loads(
        (directory / "strategy-output/studio-view.json").read_text(encoding="utf-8")
    )
    if value.get("schema_version") != "quant.etf-research-view/v1":
        raise QuantStudioError("ETF原生研究视图契约不匹配")
    return {**value, "status": "succeeded"}


def is_etf(root, identifier):
    directory = ExperimentStore(root)._run(identifier)
    value = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    return value["template_id"] == "etf-weekly-research"


def comparison_identity(view):
    """Bind comparison to verified bytes and execution rules, never path equality."""
    rows = view.get("results", [])
    recipe = view.get("recipe")
    if (
        not rows
        or not view.get("family_complete")
        or not isinstance(recipe, dict)
        or not view.get("comparison_basis")
        or any(
            row.get("status") != "completed"
            or not row.get("code_identity")
            or not row.get("input_identity")
            or not isinstance(row.get("comparison"), dict)
            or not {"execution", "currency", "frequency"} <= row["comparison"].keys()
            for row in rows
        )
    ):
        return None
    # A family uses one frozen input/runtime. Candidate strategy parameters may
    # differ, but common execution evidence must not silently vary within it.
    identities = [
        {
            "code": row["code_identity"],
            "input": row["input_identity"],
            "execution": {
                key: row["comparison"][key]
                for key in ("execution", "currency", "frequency")
            },
        }
        for row in rows
    ]
    if any(identity != identities[0] for identity in identities[1:]):
        return None
    return {
        "basis": view["comparison_basis"],
        "identity": identities[0],
        "protocol": {
            key: recipe.get(key)
            for key in ("execution", "validation", "measurement_basis", "risk")
        },
    }


def compare_body(root, identifiers):
    from quant_studio.project_web import table

    if not 1 <= len(identifiers) <= 4 or len(set(identifiers)) != len(identifiers):
        raise QuantStudioError("请选择1至4个不同实验；每项展示全部候选及失败项")
    body = (
        "<h1>四 ETF 原生实验比较</h1>"
        "<p>基准与全部候选并列保留；历史结果为回顾性证据。</p>"
    )
    bases = []
    first_view = None
    for identifier in identifiers:
        body += (
            '<section class="panel"><h2>'
            f'<a href="/runs/{identifier}">{identifier}</a></h2>'
        )
        try:
            body += context_body(root, identifier)
            view = read_family(root, identifier)
        except (QuantStudioError, OSError, ValueError, KeyError) as exc:
            body += f'<p role="alert">不可比较：{escape(str(exc))}</p></section>'
            bases.append(None)
            continue
        basis = view.get("comparison_basis")
        identity = comparison_identity(view)
        bases.append(identity)
        if identity is None:
            body += (
                '<p role="alert">身份无法核验或候选族不完整：'
                "缺少统一的冻结输入、代码或执行协议；不宣称快照一致。</p>"
            )
        body += (
            f"<p>状态：{escape(status_label(view['status']))}；"
            f"{escape(view.get('error', ''))}</p>"
        )
        if first_view is not None:
            changes = differences(
                {
                    "recipe": first_view.get(
                        "recipe", first_view.get("comparison_basis")
                    ),
                    "candidates": [
                        {
                            k: r.get(k)
                            for k in (
                                "candidate",
                                "parameters",
                                "code_identity",
                                "input_identity",
                            )
                        }
                        for r in first_view["results"]
                    ],
                },
                {
                    "recipe": view.get("recipe", basis),
                    "candidates": [
                        {
                            k: r.get(k)
                            for k in (
                                "candidate",
                                "parameters",
                                "code_identity",
                                "input_identity",
                            )
                        }
                        for r in view["results"]
                    ],
                },
            )
            body += "<h3>相对首个实验的输入、参数与版本差异</h3>"
            body += (
                table(["字段", "首个实验", "本实验"], changes)
                if changes
                else "<p>已记录字段一致；缺失的历史字段不推断。</p>"
            )
        else:
            first_view = view
        rows = []
        for item in view["results"]:
            metrics = item.get("metrics") or {}
            rows.append(
                [
                    item["candidate"],
                    status_label(item["status"]),
                    f"{metrics['total_return']:.2%}"
                    if metrics.get("total_return") is not None
                    else "不可用",
                    f"{metrics['max_drawdown']:.2%}"
                    if metrics.get("max_drawdown") is not None
                    else "不可用",
                    f"{metrics['cost_total']:,.2f}"
                    if metrics.get("cost_total") is not None
                    else "不可用",
                    str(metrics.get("fills", "不可用")),
                    str(metrics.get("sessions", "不可用")),
                    str(item.get("error") or ""),
                ]
            )
        body += table(
            [
                "候选",
                "状态",
                "扣费收益",
                "最大回撤",
                "账本费用",
                "成交笔数",
                "测试交易日",
                "失败原因",
            ],
            rows,
        )
        body += "<details><summary>原生版本、参数、事件日期与输入身份</summary><pre>"
        body += (
            escape(
                json.dumps(
                    {
                        "definition": view.get("definition_sha256"),
                        "events": view.get("attempt_dates"),
                        "candidates": [
                            {
                                k: r.get(k)
                                for k in (
                                    "candidate",
                                    "parameters",
                                    "code_identity",
                                    "input_identity",
                                )
                            }
                            for r in view["results"]
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            + "</pre></details>"
        )
        body += "<details><summary>区间、资金、成本、快照与检验协议</summary><pre>"
        body += (
            escape(
                json.dumps(
                    {
                        "experiment": basis,
                        "candidates": {
                            row["candidate"]: row.get("comparison")
                            for row in view["results"]
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            + "</pre></details></section>"
        )
    comparable = bool(bases) and all(b is not None and b == bases[0] for b in bases)
    message = (
        "区间、资金、成本、冻结输入身份、代码身份及执行/检验协议一致；"
        "仅比较候选参数的描述性结果。"
        if comparable
        else "不可比：区间、资金、费用、冻结输入、代码、执行/检验协议不同，"
        "或存在失败实验及未核验身份。未合并排名。"
    )
    return '<p role="status">' + message + "</p>" + body
